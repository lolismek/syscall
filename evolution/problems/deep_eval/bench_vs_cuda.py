#!/usr/bin/env python3
"""Benchmark Triton selective_scan kernel vs official CUDA kernel on L40S."""

import torch
import itertools
import sys
import traceback

# ─── Import our Triton kernel ───
from best_kernel import ModelNew

# ─── Import official mamba kernels ───
try:
    from mamba_ssm.ops.selective_scan_interface import selective_scan_fn, selective_scan_ref
    HAS_CUDA_KERNEL = True
except ImportError:
    print("WARNING: mamba_ssm not installed. Will only benchmark Triton kernel.")
    HAS_CUDA_KERNEL = False

def make_inputs(batch, dim, seq_len, dstate, dtype=torch.float32, device="cuda"):
    u = torch.randn(batch, dim, seq_len, dtype=dtype, device=device)
    delta = torch.randn(batch, dim, seq_len, dtype=dtype, device=device)
    A = -torch.exp(torch.randn(dim, dstate, dtype=torch.float32, device=device))
    B = torch.randn(batch, dstate, seq_len, dtype=dtype, device=device)
    C = torch.randn(batch, dstate, seq_len, dtype=dtype, device=device)
    D = torch.randn(dim, dtype=torch.float32, device=device)
    delta_bias = torch.randn(dim, dtype=torch.float32, device=device)
    return u, delta, A, B, C, D, delta_bias

def benchmark_fn(fn, warmup=10, rep=100):
    """Time a CUDA function with proper synchronization."""
    for _ in range(warmup):
        fn()
    torch.cuda.synchronize()
    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)
    start.record()
    for _ in range(rep):
        fn()
    end.record()
    torch.cuda.synchronize()
    return start.elapsed_time(end) / rep  # ms

def run_triton(model, u, delta, B, C):
    return model(u, delta, B, C)

def run_cuda(u, delta, A, B, C, D, delta_bias):
    # selective_scan_fn expects B: (batch, group, dstate, seqlen) or (batch, dstate, seqlen)
    # and similarly for C. The official interface has a specific format.
    return selective_scan_fn(
        u, delta, A, B, C, D,
        z=None,
        delta_bias=delta_bias,
        delta_softplus=True,
        return_last_state=False,
    )

def run_ref(u, delta, A, B, C, D, delta_bias):
    return selective_scan_ref(
        u, delta, A, B, C, D,
        z=None,
        delta_bias=delta_bias,
        delta_softplus=True,
        return_last_state=False,
    )

def main():
    device = "cuda"

    # Print GPU info
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"CUDA version: {torch.version.cuda}")
    print(f"PyTorch version: {torch.__version__}")
    print(f"Has official CUDA kernel: {HAS_CUDA_KERNEL}")
    print()

    # Test matrix - focused on interesting regimes
    batch_sizes = [1, 4, 16]
    dims = [768, 1536, 2560]
    seq_lens = [128, 256, 512, 1024, 2048, 4096]
    dstates = [16, 64]
    dtypes = [torch.float32]  # start with fp32 for correctness

    # Header
    print(f"{'batch':>5} {'dim':>5} {'seq':>5} {'dstate':>6} {'dtype':>8} | "
          f"{'triton_ms':>10} {'cuda_ms':>10} {'ref_ms':>10} | "
          f"{'tri_vs_cuda':>12} {'tri_vs_ref':>12} | "
          f"{'err_vs_ref':>10} {'err_vs_cuda':>12}")
    print("-" * 140)

    results = []

    for batch, dim, seq_len, dstate, dtype in itertools.product(
        batch_sizes, dims, seq_lens, dstates, dtypes
    ):
        # Skip very large configs that would OOM
        total_elems = batch * dim * seq_len
        if total_elems > 200_000_000:  # ~800MB at fp32
            continue

        try:
            u, delta, A, B, C, D, delta_bias = make_inputs(
                batch, dim, seq_len, dstate, dtype, device
            )

            # ─── Triton kernel ───
            model = ModelNew(dim=dim, dstate=dstate, delta_softplus=True).to(device)
            # Copy params to match
            model.A.data.copy_(A)
            model.D.data.copy_(D)
            model.delta_bias.data.copy_(delta_bias)

            out_triton = run_triton(model, u, delta, B, C)
            triton_ms = benchmark_fn(lambda: run_triton(model, u, delta, B, C))

            # ─── Official CUDA kernel ───
            cuda_ms = float('nan')
            out_cuda = None
            if HAS_CUDA_KERNEL:
                try:
                    # The official kernel expects B,C as (batch, group, dstate, seqlen)
                    # but also accepts (batch, dstate, seqlen) for group=1
                    out_cuda_raw = run_cuda(u, delta, A, B, C, D, delta_bias)
                    # selective_scan_fn may return a tuple
                    if isinstance(out_cuda_raw, tuple):
                        out_cuda = out_cuda_raw[0]
                    else:
                        out_cuda = out_cuda_raw
                    cuda_ms = benchmark_fn(lambda: run_cuda(u, delta, A, B, C, D, delta_bias))
                except Exception as e:
                    print(f"  CUDA kernel failed for config ({batch},{dim},{seq_len},{dstate}): {e}")

            # ─── Reference (PyTorch) ───
            ref_ms = float('nan')
            out_ref = None
            if HAS_CUDA_KERNEL:
                try:
                    out_ref_raw = run_ref(u, delta, A, B, C, D, delta_bias)
                    if isinstance(out_ref_raw, tuple):
                        out_ref = out_ref_raw[0]
                    else:
                        out_ref = out_ref_raw
                    # Only benchmark ref for small configs (it's slow)
                    if seq_len <= 1024:
                        ref_ms = benchmark_fn(lambda: run_ref(u, delta, A, B, C, D, delta_bias), warmup=3, rep=20)
                    else:
                        ref_ms = benchmark_fn(lambda: run_ref(u, delta, A, B, C, D, delta_bias), warmup=2, rep=5)
                except Exception as e:
                    print(f"  Ref kernel failed for config ({batch},{dim},{seq_len},{dstate}): {e}")

            # ─── Correctness ───
            err_vs_ref = float('nan')
            err_vs_cuda = float('nan')
            if out_ref is not None:
                err_vs_ref = torch.max(torch.abs(out_triton.float() - out_ref.float())).item()
            if out_cuda is not None:
                err_vs_cuda = torch.max(torch.abs(out_triton.float() - out_cuda.float())).item()

            # ─── Speedup ───
            tri_vs_cuda = f"{cuda_ms / triton_ms:.2f}x" if not (cuda_ms != cuda_ms) else "N/A"
            tri_vs_ref = f"{ref_ms / triton_ms:.2f}x" if not (ref_ms != ref_ms) else "N/A"

            print(f"{batch:>5} {dim:>5} {seq_len:>5} {dstate:>6} {'fp32':>8} | "
                  f"{triton_ms:>10.3f} {cuda_ms:>10.3f} {ref_ms:>10.3f} | "
                  f"{tri_vs_cuda:>12} {tri_vs_ref:>12} | "
                  f"{err_vs_ref:>10.2e} {err_vs_cuda:>12.2e}")

            results.append({
                'batch': batch, 'dim': dim, 'seq_len': seq_len, 'dstate': dstate,
                'triton_ms': triton_ms, 'cuda_ms': cuda_ms, 'ref_ms': ref_ms,
                'err_vs_ref': err_vs_ref, 'err_vs_cuda': err_vs_cuda,
            })

            # Free memory
            del model, u, delta, A, B, C, D, delta_bias, out_triton
            if out_cuda is not None: del out_cuda
            if out_ref is not None: del out_ref
            torch.cuda.empty_cache()

        except Exception as e:
            print(f"  FAILED ({batch},{dim},{seq_len},{dstate}): {e}")
            traceback.print_exc()
            torch.cuda.empty_cache()

    # ─── Summary ───
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    if not results:
        print("No results collected!")
        return

    wins = [r for r in results if not (r['cuda_ms'] != r['cuda_ms']) and r['triton_ms'] < r['cuda_ms']]
    losses = [r for r in results if not (r['cuda_ms'] != r['cuda_ms']) and r['triton_ms'] >= r['cuda_ms']]

    print(f"\nTriton wins: {len(wins)} / {len(wins)+len(losses)} configs")

    if wins:
        print("\nBest wins (Triton faster than CUDA):")
        wins.sort(key=lambda r: r['cuda_ms'] / r['triton_ms'], reverse=True)
        for r in wins[:10]:
            speedup = r['cuda_ms'] / r['triton_ms']
            print(f"  {speedup:.2f}x faster | batch={r['batch']}, dim={r['dim']}, "
                  f"seq={r['seq_len']}, dstate={r['dstate']}")

    if losses:
        print("\nWorst losses (CUDA faster than Triton):")
        losses.sort(key=lambda r: r['triton_ms'] / r['cuda_ms'], reverse=True)
        for r in losses[:10]:
            slowdown = r['triton_ms'] / r['cuda_ms']
            print(f"  {slowdown:.2f}x slower | batch={r['batch']}, dim={r['dim']}, "
                  f"seq={r['seq_len']}, dstate={r['dstate']}")

    # Correctness summary
    max_err_ref = max((r['err_vs_ref'] for r in results if not (r['err_vs_ref'] != r['err_vs_ref'])), default=float('nan'))
    max_err_cuda = max((r['err_vs_cuda'] for r in results if not (r['err_vs_cuda'] != r['err_vs_cuda'])), default=float('nan'))
    print(f"\nMax error vs reference: {max_err_ref:.2e}")
    print(f"Max error vs CUDA:     {max_err_cuda:.2e}")

if __name__ == "__main__":
    main()
