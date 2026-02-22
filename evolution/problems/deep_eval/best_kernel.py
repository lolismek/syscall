import torch
import torch.nn as nn
import triton
import triton.language as tl

@triton.jit
def selective_scan_kernel(
    u_ptr, delta_ptr, A_ptr, B_ptr, C_ptr, D_ptr, delta_bias_ptr,
    out_ptr,
    batch_size, seq_len, dim, dstate,
    delta_softplus: tl.constexpr,
    BLOCK_DIM: tl.constexpr,
    BLOCK_DSTATE: tl.constexpr
):
    pid_batch = tl.program_id(0)
    pid_dim = tl.program_id(1)
    
    # Stride calculations
    u_stride_batch = dim * seq_len
    u_stride_dim = seq_len
    u_stride_seq = 1
    
    delta_stride_batch = dim * seq_len
    delta_stride_dim = seq_len
    delta_stride_seq = 1
    
    A_stride_dim = dstate
    A_stride_dstate = 1
    
    B_stride_batch = dstate * seq_len
    B_stride_dstate = seq_len
    B_stride_seq = 1
    
    C_stride_batch = dstate * seq_len
    C_stride_dstate = seq_len
    C_stride_seq = 1
    
    out_stride_batch = dim * seq_len
    out_stride_dim = seq_len
    out_stride_seq = 1
    
    # Offsets
    dim_idx = pid_dim * BLOCK_DIM + tl.arange(0, BLOCK_DIM)
    dstate_idx = tl.arange(0, BLOCK_DSTATE)
    
    dim_mask = dim_idx < dim
    dstate_mask = dstate_idx < dstate
    
    # Pointers to parameter vectors
    A_ptrs = A_ptr + dim_idx[:, None] * A_stride_dim + dstate_idx[None, :] * A_stride_dstate
    D_ptrs = D_ptr + dim_idx
    delta_bias_ptrs = delta_bias_ptr + dim_idx
    
    # Load parameters
    A_vals = tl.load(A_ptrs, mask=dim_mask[:, None] & dstate_mask[None, :], other=0.0)
    D_val = tl.load(D_ptrs, mask=dim_mask, other=0.0)
    delta_bias_val = tl.load(delta_bias_ptrs, mask=dim_mask, other=0.0)
    
    # Initialize hidden state
    x = tl.zeros([BLOCK_DIM, BLOCK_DSTATE], dtype=tl.float32)
    
    # Process sequence
    for t in range(seq_len):
        # Calculate offsets for current timestep
        u_offs = pid_batch * u_stride_batch + dim_idx * u_stride_dim + t * u_stride_seq
        delta_offs = pid_batch * delta_stride_batch + dim_idx * delta_stride_dim + t * delta_stride_seq
        B_offs = pid_batch * B_stride_batch + dstate_idx * B_stride_dstate + t * B_stride_seq
        C_offs = pid_batch * C_stride_batch + dstate_idx * C_stride_dstate + t * C_stride_seq
        out_offs = pid_batch * out_stride_batch + dim_idx * out_stride_dim + t * out_stride_seq
        
        # Load data
        u_val = tl.load(u_ptr + u_offs, mask=dim_mask, other=0.0)
        delta_val = tl.load(delta_ptr + delta_offs, mask=dim_mask, other=0.0)
        B_val = tl.load(B_ptr + B_offs, mask=dstate_mask, other=0.0)
        C_val = tl.load(C_ptr + C_offs, mask=dstate_mask, other=0.0)
        
        # Apply delta bias and optional softplus
        delta_val = delta_val + delta_bias_val
        if delta_softplus:
            delta_val = tl.where(delta_val >= 0, 
                                delta_val + tl.log(tl.exp(-delta_val) + 1.0),
                                tl.log(tl.exp(delta_val) + 1.0))
        
        # Compute deltaA and deltaB_u
        deltaA = tl.exp(delta_val[:, None] * A_vals)
        deltaB_u = delta_val[:, None] * B_val[None, :] * u_val[:, None]
        
        # Update hidden state
        x = deltaA * x + deltaB_u
        
        # Compute output
        y = tl.sum(x * C_val[None, :], axis=1) + D_val * u_val
        
        # Store output
        tl.store(out_ptr + out_offs, y, mask=dim_mask)

class ModelNew(nn.Module):
    def __init__(self, dim: int = 768, dstate: int = 16, delta_softplus: bool = True):
        super().__init__()
        self.dim = dim
        self.dstate = dstate
        self.delta_softplus = delta_softplus
        
        # Parameters
        self.A = nn.Parameter(-torch.exp(torch.randn(dim, dstate)))
        self.D = nn.Parameter(torch.randn(dim))
        self.delta_bias = nn.Parameter(torch.randn(dim))
    
    def forward(self, u: torch.Tensor, delta: torch.Tensor,
                B: torch.Tensor, C: torch.Tensor) -> torch.Tensor:
        """
        Optimized selective scan forward pass using Triton.
        u:     (batch, dim, seqlen) - input
        delta: (batch, dim, seqlen) - time step
        B:     (batch, dstate, seqlen) - input-dependent state transition
        C:     (batch, dstate, seqlen) - input-dependent output projection
        Returns: (batch, dim, seqlen) - output
        """
        batch, dim, seqlen = u.shape
        dstate = self.dstate
        
        # Ensure inputs are contiguous
        u = u.contiguous()
        delta = delta.contiguous()
        B = B.contiguous()
        C = C.contiguous()
        
        # Output tensor
        out = torch.empty_like(u)
        
        # Grid configuration
        grid = (batch, triton.cdiv(dim, 32))
        
        # Launch kernel
        selective_scan_kernel[grid](
            u, delta, self.A, B, C, self.D, self.delta_bias,
            out,
            batch, seqlen, dim, dstate,
            self.delta_softplus,
            BLOCK_DIM=32,
            BLOCK_DSTATE=triton.next_power_of_2(dstate)
        )
        
        return out