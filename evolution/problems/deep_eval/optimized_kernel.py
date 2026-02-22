import torch
import torch.nn as nn
import triton
import triton.language as tl


@triton.jit
def selective_scan_fwd(
    u_ptr, delta_ptr, A_ptr, B_ptr, C_ptr, D_ptr, dbias_ptr, out_ptr,
    batch_size, seq_len, dim, dstate,
    s_u_b, s_u_d, s_u_s,
    s_dt_b, s_dt_d, s_dt_s,
    s_o_b, s_o_d, s_o_s,
    s_B_b, s_B_n, s_B_s,
    s_C_b, s_C_n, s_C_s,
    s_A_d, s_A_n,
    DELTA_SOFTPLUS: tl.constexpr,
    HAS_D: tl.constexpr,
    BLOCK_N: tl.constexpr,
):
    pid = tl.program_id(0)
    b = pid // dim
    d = pid  % dim

    if b >= batch_size:
        return

    LOG2E: tl.constexpr  = 1.4426950408889634
    LN2:   tl.constexpr  = 0.6931471805599453

    n_idx = tl.arange(0, BLOCK_N)
    n_mask = n_idx < dstate

    A = tl.load(A_ptr + d * s_A_d + n_idx * s_A_n,
                mask=n_mask, other=0.0).to(tl.float32)
    A_log2e = A * LOG2E

    dbias = tl.load(dbias_ptr + d).to(tl.float32)

    D_val = tl.zeros([1], dtype=tl.float32)
    if HAS_D:
        D_val = tl.load(D_ptr + d).to(tl.float32)

    u_base  = u_ptr     + b * s_u_b  + d * s_u_d
    dt_base = delta_ptr + b * s_dt_b + d * s_dt_d
    B_base  = B_ptr     + b * s_B_b
    C_base  = C_ptr     + b * s_C_b
    o_base  = out_ptr   + b * s_o_b  + d * s_o_d

    x = tl.zeros([BLOCK_N], dtype=tl.float32)

    for t in range(seq_len):
        u_t  = tl.load(u_base  + t * s_u_s).to(tl.float32)
        dt_t = tl.load(dt_base + t * s_dt_s).to(tl.float32)

        dt_t = dt_t + dbias
        if DELTA_SOFTPLUS:
            abs_dt = tl.abs(dt_t)
            dt_t = tl.maximum(dt_t, 0.0) + tl.math.log2(1.0 + tl.exp2(-abs_dt * LOG2E)) * LN2

        B_t = tl.load(B_base + n_idx * s_B_n + t * s_B_s,
                       mask=n_mask, other=0.0).to(tl.float32)
        C_t = tl.load(C_base + n_idx * s_C_n + t * s_C_s,
                       mask=n_mask, other=0.0).to(tl.float32)

        dA = tl.exp2(dt_t * A_log2e)
        dBu = (dt_t * u_t) * B_t
        x = dA * x + dBu

        y = tl.sum(x * C_t, axis=0)
        if HAS_D:
            y = y + D_val * u_t
        tl.store(o_base + t * s_o_s, y)


class ModelNew(nn.Module):
    def __init__(self, dim: int = 768, dstate: int = 16, delta_softplus: bool = True):
        super().__init__()
        self.dim = dim
        self.dstate = dstate
        self.delta_softplus = delta_softplus

        self.A = nn.Parameter(-torch.exp(torch.randn(dim, dstate)))
        self.D = nn.Parameter(torch.randn(dim))
        self.delta_bias = nn.Parameter(torch.randn(dim))

    def forward(self, u, delta, B, C):
        batch, dim, seqlen = u.shape

        u     = u.contiguous()
        delta = delta.contiguous()
        B     = B.contiguous()
        C     = C.contiguous()
        out   = torch.empty_like(u)

        BLOCK_N = triton.next_power_of_2(self.dstate)
        n_programs = batch * dim

        selective_scan_fwd[(n_programs,)](
            u, delta, self.A, B, C, self.D, self.delta_bias, out,
            batch, seqlen, dim, self.dstate,
            u.stride(0),     u.stride(1),     u.stride(2),
            delta.stride(0), delta.stride(1), delta.stride(2),
            out.stride(0),   out.stride(1),   out.stride(2),
            B.stride(0),     B.stride(1),     B.stride(2),
            C.stride(0),     C.stride(1),     C.stride(2),
            self.A.stride(0), self.A.stride(1),
            self.delta_softplus,
            True,
            BLOCK_N,
            num_warps=1,
            num_stages=1,
        )
        return out
