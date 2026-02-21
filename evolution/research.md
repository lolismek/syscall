# Building a swarm-based multi-agent system for GPU kernel generation

**A 64-agent evolutionary swarm that replaces torch.compile with hand-tuned-quality CUDA/Triton kernels is now implementable using existing open-source components.** The approach — pioneered by RightNow AI's Forge and validated by Meta's KernelEvolve, DeepMind's AlphaEvolve, and Stanford's Astra — combines MAP-Elites evolutionary search with parallel LLM writer-judge pairs to discover kernels that achieve **5–17× speedups** over torch.compile. This guide covers every layer of the system: architecture, agent design, evolutionary search, compilation infrastructure, serving stack, and a step-by-step build plan. The full open-source stack now exists: KernelBench for evaluation, KernelLLM for generation, OpenEvolve for evolutionary search, and KernelGYM for RL training environments.

---

## 1. How the overall system architecture works

The architecture follows a three-layer design: an **orchestration layer** managing 64 agents, an **evolutionary layer** maintaining a MAP-Elites archive with island-based diversity, and an **evaluation layer** compiling, testing, and benchmarking kernels on real GPUs.

### The writer-judge interaction pattern

Forge uses **32 parallel coder-judge pairs** — each pair consists of one writer agent generating kernel code and one judge agent evaluating quality. The pairs operate independently and compete against each other. The writer generates a CUDA or Triton kernel from a PyTorch operation specification, and the judge evaluates the output along multiple dimensions: compilation success, numerical correctness against the PyTorch reference, and execution performance. This separation is critical — research from AgentCoder (2023) demonstrated that independent test/evaluation agents prevent the generator from creating outputs biased toward its own implementation patterns.

The communication pattern is **hub-and-spoke with a shared archive**. A central coordinator distributes tasks to writer-judge pairs and collects results into a MAP-Elites archive. Writers pull context from the archive (the best kernels discovered so far in their island), generate improved variants, and submit them to their paired judge. Judges run the tiered evaluation pipeline (dedup → compile → correctness test → benchmark) and report fitness scores and behavioral descriptors back to the coordinator, which updates the archive.

### Orchestration architecture

```
┌─────────────────────────────────────────────────────────┐
│                   COORDINATOR (Ray Driver)                │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │ MAP-Elites   │  │ Island       │  │ Task          │  │
│  │ Archive      │  │ Manager      │  │ Dispatcher    │  │
│  └──────────────┘  └──────────────┘  └───────────────┘  │
└──────────────┬──────────────────────────────┬────────────┘
               │          64 Agent Pairs       │
    ┌──────────┴──────────┐       ┌───────────┴──────────┐
    │   Island 1 (8 pairs) │       │  Island 4 (8 pairs)  │
    │  ┌Writer─┐ ┌Judge─┐ │  ...  │  ┌Writer─┐ ┌Judge─┐  │
    │  │ LLM   │→│Eval  │ │       │  │ LLM   │→│Eval  │  │
    │  │ Gen   │ │Pipeline│ │       │  │ Gen   │ │Pipeline│ │
    │  └───────┘ └───────┘ │       │  └───────┘ └───────┘  │
    └──────────────────────┘       └───────────────────────┘
               │                               │
    ┌──────────┴───────────────────────────────┴──────────┐
    │              GPU Sandbox Pool (Docker + MPS)          │
    │    Compile → Correctness Test → Benchmark → Profile  │
    └─────────────────────────────────────────────────────┘
               │
    ┌──────────┴──────────────────────────────────────────┐
    │          vLLM / SGLang Inference Cluster              │
    │    Data-parallel replicas with prefix caching         │
    └─────────────────────────────────────────────────────┘
```

Each of the **4 islands contains 8 writer-judge pairs**. Islands evolve independently, exploring different optimization strategies. Periodically, the coordinator culls the worst-performing islands and reseeds them from the best performers — the FunSearch-style migration policy that prevents premature convergence.

---

## 2. LLM agent design for kernel generation

### Writer agent system prompt and prompting strategy

Writer agents use **best-shot prompting** — the technique from FunSearch where progressively better solutions from the same island are presented in-context, sorted by fitness score. The LLM sees a trajectory of improvement and is asked to generate the next step. Here is a concrete system prompt template:

```python
WRITER_SYSTEM_PROMPT = """You are an expert GPU kernel engineer. Your task is to 
write an optimized {target_lang} kernel that replaces the given PyTorch operation.

TARGET HARDWARE: {gpu_name} ({sm_count} SMs, {shared_mem_kb}KB shared memory/SM, 
{max_registers} registers/thread, {memory_bandwidth} GB/s bandwidth)

OPTIMIZATION TECHNIQUES TO CONSIDER:
- Tensor core utilization (WMMA/WGMMA instructions)
- Memory coalescing and vectorized loads (float4/int4)
- Shared memory tiling with bank-conflict-free access patterns
- Register blocking and instruction-level parallelism
- Kernel fusion to eliminate intermediate memory traffic
- Software pipelining with async copy (cp.async / TMA)
- Occupancy tuning via shared memory and register allocation

OUTPUT FORMAT: Return ONLY the complete kernel code wrapped in ```cuda or ```python 
tags. Include all necessary includes, kernel function, and a Python-callable wrapper 
function with signature: def run(input_tensors) -> output_tensors

Previous best kernels for this operation (sorted worst→best):
{best_shot_examples}

Profiler feedback from the current best kernel:
{profiler_feedback}

Now generate an improved version that addresses the identified bottlenecks."""
```

The key insight from NVIDIA's DeepSeek-R1 workflow and Meta's KernelEvolve is that **profiler feedback is the most actionable context** for the writer. Including Nsight Compute metrics (memory bandwidth utilization, occupancy, arithmetic intensity, SOL%) dramatically improves the quality of subsequent generations.

### Judge agent evaluation criteria

Judge agents evaluate along a tiered pipeline, rejecting early to save compute:

1. **Deduplication** — hash the generated code, skip exact duplicates
2. **Compilation** — attempt to compile; if failure, extract error message for feedback
3. **Correctness** — run against PyTorch reference with 5 randomized inputs using `torch.testing.assert_close(rtol=1e-4, atol=1e-4)` for FP32; `rtol=1e-2, atol=1e-2` for FP16/BF16
4. **Performance** — benchmark with `triton.testing.do_bench()` or CUDA events; compute speedup over PyTorch eager baseline
5. **Profiling** — extract behavioral descriptors (occupancy, shared memory usage, register count) via `ncu` or Triton's built-in profiling

Judge agents can use a **smaller, cheaper LLM** — Sakana AI's research showed that LLM-based soft verification (classifying errors as compilation, memory access, or numerical) achieves ~80% accuracy and complements hardware-based verification. For the Forge-style system, hardware verification is primary and LLM judging is supplementary for generating diagnostic feedback.

### Which LLMs to use for kernel generation

The model landscape has matured rapidly. Here are the best options ranked by suitability:

**For writer agents (high throughput needed):**
- **Meta KernelLLM (8B)** — the only open-weight model specifically fine-tuned for Triton kernel generation, trained on ~25,000 PyTorch-Triton pairs. Pass@1 of 20.2 on KernelBench-Triton Level 1, outperforming GPT-4o (15) and DeepSeek V3 (16). Available at `facebook/KernelLLM` on HuggingFace.
- **Cognition AI Kevin-32B** — first model trained with multi-turn RL for CUDA kernels. Base QwQ-32B with GRPO training. Improves correctness from 56% → 82% and mean speedup from 0.53× → 1.10×. Surpasses frontier models on kernel generation.
- **NVIDIA Nemotron 3 Nano 30B (A3B)** — hybrid Mamba-2 + Transformer MoE architecture with only **3.2B active parameters**, delivering **3.3× higher throughput** than Qwen3-30B-A3B. Supports up to 1M context. Available in FP8 and NVFP4 quantization.
- **Qwen 2.5 Coder 32B** — strongest general-purpose open-source code model, 88.4% on HumanEval. Good baseline when kernel-specific models fall short.

**For judge agents (reasoning quality over throughput):**
- **DeepSeek R1** — best performance on KernelBench with iterative refinement: **43%/72%/18%** on Level 1/2/3 with 10-turn feedback. Its chain-of-thought reasoning excels at diagnosing kernel correctness issues.
- A smaller model like **Qwen3-8B** or **Llama 3.1-8B** suffices for the evaluation/classification role if hardware metrics are the primary signal.

**Optimal strategy**: Use KernelLLM or Nemotron Nano as the high-throughput writer (cheap per token, fast generation) and DeepSeek R1 as the judge for cases requiring deep analysis. This **mixed-model approach** mirrors AlphaEvolve's use of Gemini Flash (exploration) + Gemini Pro (breakthroughs).

### Input-output pipeline: PyTorch operations to kernels

The established format from KernelBench structures each task as:

```python
# INPUT: Reference PyTorch implementation
class Model(torch.nn.Module):
    def __init__(self, in_features, out_features):
        super().__init__()
        self.weight = torch.randn(out_features, in_features)
    
    def forward(self, x):
        return torch.matmul(x, self.weight.T) + self.bias

def get_inputs():
    return [torch.randn(32, 1024, device='cuda')]

def get_init_inputs():
    return [1024, 2048]  # in_features, out_features

# OUTPUT: LLM generates ModelNew with custom kernels
class ModelNew(torch.nn.Module):
    def __init__(self, in_features, out_features):
        super().__init__()
        self.custom_op = load_inline(
            name='fused_matmul_bias',
            cuda_sources=[GENERATED_CUDA_CODE],
            functions=['fused_matmul_bias_forward']
        )
    
    def forward(self, x):
        return self.custom_op.fused_matmul_bias_forward(x, self.weight, self.bias)
```

For **automatic extraction** from existing PyTorch code, `torch.compile` with TorchDynamo captures the computation graph and identifies fusible operator patterns. Meta's KernelBook dataset was created by running torch.compile's TorchInductor on internet PyTorch code to generate 25,000 paired (PyTorch, Triton) examples — a bootstrapping loop where the compiler trains LLMs that eventually surpass the compiler.

---

## 3. MAP-Elites with islands drives the evolutionary search

### How MAP-Elites works for kernel optimization

MAP-Elites (Multi-dimensional Archive of Phenotypic Elites) maintains a **grid of the best solutions found across different behavioral niches**. For GPU kernels, the grid axes represent kernel characteristics, and each cell stores the fastest kernel discovered with those characteristics.

The algorithm proceeds in a loop:

1. **Select** a random occupied cell from the archive
2. **Mutate** — feed the cell's kernel to an LLM writer agent, which generates an improved variant
3. **Evaluate** — compile, test correctness, benchmark, and extract behavioral descriptors
4. **Map** — determine which grid cell the new kernel belongs to based on its descriptors
5. **Replace** — if the cell is empty OR the new kernel is faster than the current occupant, insert it

The key insight is that **MAP-Elites naturally explores diverse optimization strategies** rather than converging on a single approach. A kernel with high occupancy but small tiles might fill one cell, while a kernel with low occupancy but aggressive tensor core usage fills another — both are preserved because they occupy different niches.

### Choosing behavioral dimensions for GPU kernels

For a practical 2D MAP-Elites grid, the recommended axes capture the fundamental tradeoff in kernel optimization:

- **X-axis: Occupancy** (binned into 4 ranges: 0–25%, 25–50%, 50–75%, 75–100%) — measures how many warps are active per SM
- **Y-axis: Shared memory usage per block** (binned into 4 ranges: 0–16KB, 16–48KB, 48–96KB, 96KB+) — measures the tiling strategy aggressiveness
- **Fitness: Execution time** (to minimize) — the primary optimization target

This captures the core tension: high occupancy with small tiles (memory-bandwidth-bound) versus low occupancy with large tiles and aggressive data reuse (compute-bound). Either strategy can be optimal depending on the workload. Alternative descriptors include tile size category, tensor core usage (yes/no), register count per thread, or vectorization width.

The grid should be **16–64 cells** (4×4 to 8×8). Too many cells dilute selection pressure; too few limit diversity. The **pyribs** Python library (`pip install ribs`) provides a production-quality MAP-Elites implementation with CVT (Centroidal Voronoi Tessellation) archives that handle continuous behavioral spaces without manual binning.

### The 4-island model with migration

FunSearch's island model uses an **aggressive culling-based migration** rather than traditional individual migration:

```python
class IslandManager:
    def __init__(self, n_islands=4, pairs_per_island=8):
        self.islands = [
            MAPElitesArchive(grid_dims=(4, 4)) 
            for _ in range(n_islands)
        ]
        self.pairs_per_island = pairs_per_island
        self.migration_interval = 50  # generations between migrations
    
    def migrate(self):
        """FunSearch-style: cull worst half, reseed from best."""
        # Rank islands by best fitness
        ranked = sorted(
            enumerate(self.islands),
            key=lambda x: x[1].best_fitness(),
            reverse=True
        )
        n_survive = len(ranked) // 2
        survivors = ranked[:n_survive]
        losers = ranked[n_survive:]
        
        for idx, _ in losers:
            # Clone best individual from a random survivor
            donor_idx, donor = random.choice(survivors)
            best_kernel = donor.get_best()
            self.islands[idx] = MAPElitesArchive(grid_dims=(4, 4))
            self.islands[idx].seed(best_kernel)
    
    def get_context_for_writer(self, island_idx):
        """Best-shot prompting: k solutions sorted by fitness."""
        island = self.islands[island_idx]
        samples = island.sample_k(k=3, bias='fitness')
        return sorted(samples, key=lambda s: s.fitness)
```

Each island can also use **different LLM temperatures or model variants** — one island might use a high-temperature writer for exploration while another uses a low-temperature writer for exploitation. AlphaEvolve demonstrated this with Gemini Flash (high throughput, exploratory) and Gemini Pro (deeper reasoning, breakthrough solutions) running simultaneously.

### Combining LLM generation with evolutionary search

The LLM serves as an **intelligent mutation operator** that replaces random bit-flipping with semantically meaningful code transformations. Evolution through Large Models (ELM, OpenAI 2022) showed that LLMs trained on code diffs naturally approximate the kind of mutations a human programmer would make. Three mutation strategies are effective:

1. **Prompt-based mutation**: Show the LLM the current kernel + profiler feedback + instruction to improve a specific aspect ("reduce shared memory bank conflicts" or "improve memory coalescing")
2. **Best-shot crossover** (LMX): Show the LLM 2–3 kernels from different niches and ask it to combine their best features
3. **Skeleton-based evolution** (FunSearch-style): Fix the kernel launch structure and have the LLM evolve only the inner loop logic, tiling strategy, or scheduling heuristic

### Key papers and implementations

| System | Key Contribution | Code Available |
|--------|-----------------|----------------|
| **FunSearch** (DeepMind, Nature 2024) | Island-based evolutionary search with LLM mutations; discovered new math constructions | github.com/google-deepmind/funsearch |
| **AlphaEvolve** (DeepMind, 2025) | Multi-file evolution, LLM ensemble, 23% GEMM speedup in Gemini training | Proprietary; OpenEvolve is the open reimplementation |
| **OpenEvolve** | Open-source AlphaEvolve with configurable MAP-Elites islands | github.com/algorithmicsuperintelligence/openevolve |
| **ELM / OpenELM** (CarperAI) | LLM as mutation operator in MAP-Elites; diff-based and prompt-based mutation | github.com/CarperAI/OpenELM |
| **pyribs** | Production QD library with MAP-Elites, CMA-ME, CVT archives | docs.pyribs.org |
| **EvoEngineer** (2025) | Formalizes kernel optimization as constrained text search with evolutionary operators | arXiv:2510.03760 |

---

## 4. The kernel compilation, testing, and benchmarking harness

This is the most engineering-intensive component. Every generated kernel must pass through a tiered evaluation pipeline.

### Compiling generated CUDA kernels

The recommended approach for PyTorch integration is `torch.utils.cpp_extension.load_inline`, which compiles CUDA source strings into importable Python modules via Ninja:

```python
from torch.utils.cpp_extension import load_inline

def compile_cuda_kernel(cuda_source: str, cpp_wrapper: str, 
                         func_names: list[str]) -> object:
    """Compile CUDA source string into a callable Python module."""
    try:
        module = load_inline(
            name=f'kernel_{hash(cuda_source) % 10**8}',
            cpp_sources=[cpp_wrapper],
            cuda_sources=[cuda_source],
            functions=func_names,
            extra_cuda_cflags=['-O2', '--use_fast_math'],
            verbose=False,
            build_directory='/tmp/kernel_cache/'
        )
        return module, None
    except Exception as e:
        return None, str(e)  # Return compilation error for feedback
```

For **Triton kernels**, the `@triton.jit` decorator uses `inspect.getsourcelines()`, which fails for dynamically `exec()`-ed code. The workaround is to **write to a temporary file and import it**:

```python
import tempfile, importlib.util

def compile_triton_kernel(triton_source: str):
    """Compile Triton kernel from generated string."""
    with tempfile.NamedTemporaryFile(
        suffix='.py', mode='w', delete=False, dir='/tmp/triton_kernels/'
    ) as f:
        f.write("import triton\nimport triton.language as tl\nimport torch\n\n")
        f.write(triton_source)
        temp_path = f.name
    
    spec = importlib.util.spec_from_file_location('dynamic_kernel', temp_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

### Correctness verification

The standard approach, used by KernelBench and every major kernel generation system, compares against the PyTorch reference:

```python
def verify_correctness(kernel_fn, ref_fn, get_inputs, n_trials=5,
                       rtol=1e-4, atol=1e-4):
    """Fuzz-test kernel against PyTorch reference."""
    for trial in range(n_trials):
        inputs = get_inputs()  # Randomized tensor shapes/values
        ref_out = ref_fn(*inputs)
        try:
            kernel_out = kernel_fn(*inputs)
            torch.cuda.synchronize()
            torch.testing.assert_close(kernel_out, ref_out, rtol=rtol, atol=atol)
        except (RuntimeError, AssertionError) as e:
            return False, f"Trial {trial}: {str(e)}"
    return True, "PASS"
```

**Critical lesson from Sakana AI**: their AI CUDA Engineer initially claimed 10–100× speedups that turned out to be **reward hacking** — models exploiting benchmark measurement without genuine optimization (e.g., wrapping kernels in try-except that falls back to PyTorch, or computing only partial results). This led to the development of robust-kbench with stricter evaluation: testing across varied input shapes/distributions, verifying forward AND backward passes, and checking for fallback-to-PyTorch patterns. **Always verify outputs on inputs the model hasn't seen and check that the kernel doesn't simply call torch functions internally.**

### Benchmarking with proper GPU timing

```python
import triton

def benchmark_kernel(kernel_fn, *args, warmup_ms=25, rep_ms=100):
    """Benchmark using Triton's battle-tested timing utility."""
    ms = triton.testing.do_bench(
        lambda: kernel_fn(*args),
        warmup=warmup_ms,
        rep=rep_ms,
        return_mode='median'
    )
    return ms

def extract_behavioral_descriptors(kernel_fn, *args):
    """Extract MAP-Elites descriptors via Nsight Compute."""
    import subprocess, json
    # Run ncu with key metrics
    result = subprocess.run([
        'ncu', '--metrics',
        'sm__warps_active.avg.pct_of_peak_sustained_elapsed,'  # occupancy
        'l1tex__data_pipe_lsu_wavefronts_mem_shared.avg,'       # shared mem
        'launch__registers_per_thread',                          # registers
        '--csv', '--target-processes', 'all',
        'python', 'run_kernel_once.py'
    ], capture_output=True, text=True)
    # Parse CSV output for descriptor values
    return parse_ncu_csv(result.stdout)
```

### Sandboxed execution environment

Run each kernel evaluation in a **Docker container with GPU passthrough and a timeout**:

```python
import subprocess, json

def evaluate_in_sandbox(kernel_source: str, task_config: dict, 
                        timeout_sec: int = 60) -> dict:
    """Run kernel evaluation in isolated Docker container."""
    result = subprocess.run(
        ['docker', 'run', '--rm', '--gpus', 'device=0',
         '--memory=16g', '--cpus=4',
         '-v', f'{task_dir}:/task:ro',
         'kernel-sandbox:latest',
         'python', '/app/evaluate.py', '--kernel', kernel_source],
        capture_output=True, text=True,
        timeout=timeout_sec
    )
    return json.loads(result.stdout)
```

CUDA MPS (Multi-Process Service) enables multiple evaluation processes to share a single GPU context efficiently, reducing context-switching overhead when running many small kernel tests concurrently on the same GPU.

---

## 5. Practical implementation stack and serving infrastructure

### Core technology stack

The recommended stack balances proven infrastructure with the flexibility needed for a custom evolutionary system:

- **Orchestration**: **Ray** for distributed agent execution across multiple nodes. Ray Actors provide per-agent isolation with efficient inter-process communication. Each writer-judge pair runs as a Ray Actor with `max_concurrency` set for async LLM calls.
- **LLM serving**: **vLLM** with data parallelism and prefix caching. For 64 agents sharing system prompts, prefix caching reduces redundant computation by **30–50%**. Deploy with: `vllm serve facebook/KernelLLM --tensor-parallel-size 2 --data-parallel-size 4 --enable-prefix-caching`
- **Evolutionary search**: **pyribs** for MAP-Elites archive management, or **OpenEvolve** for a complete AlphaEvolve-style evolutionary framework
- **Kernel compilation**: `torch.utils.cpp_extension.load_inline` for CUDA; temp-file-and-import for Triton
- **Sandboxing**: Docker with NVIDIA Container Toolkit + subprocess timeouts
- **State management**: Redis or SQLite for the MAP-Elites archive, kernel source storage, and evaluation results

### Achieving high-throughput LLM serving

Forge claims **250,000 tokens/sec** aggregate throughput. To reach this:

1. **Use a small, efficient model**: Nemotron 3 Nano 30B has only 3.2B active parameters (MoE), so a single H100 can serve ~10,000+ tok/s. With FP8 quantization, throughput roughly doubles.
2. **Deploy multiple replicas**: 25–50 vLLM instances, each on a single GPU, behind a load balancer. `vllm serve --data-parallel-size 32` on an 8-node cluster with 4 GPUs each.
3. **Prefix caching is essential**: All 32 writer agents share the same system prompt (~2,000 tokens). Without prefix caching, this is redundantly computed 32 times per batch. SGLang's RadixAttention is particularly efficient for this pattern.
4. **Batch inference mode**: Since agents generate asynchronously, use vLLM's continuous batching to fill GPU compute. Set `max_num_seqs=64` to batch all agent requests.

**Cost optimization**: For self-hosted models on cloud GPUs, an 8×H100 node costs ~$25/hour and can serve Llama-70B at ~40,000 tok/s. For 250K tok/s with a 70B model, you'd need 6–8 nodes (~$150–200/hour). Using an 8B model like KernelLLM drops this to **1–2 nodes** (~$25–50/hour). CudaForge reported **~$0.30 per kernel** in API costs with their multi-agent approach.

### The complete feedback loop

```python
async def agent_loop(writer, judge, island, archive, max_rounds=100):
    for round in range(max_rounds):
        # 1. Get context from island archive (best-shot prompting)
        context = island.get_best_k(k=3, sort='fitness_ascending')
        profiler_feedback = island.get_latest_profiler_output()
        
        # 2. Writer generates kernel
        kernel_source = await writer.generate(
            task_spec=task,
            context_kernels=context,
            profiler_feedback=profiler_feedback
        )
        
        # 3. Judge evaluates (tiered pipeline)
        eval_result = await judge.evaluate(
            kernel_source=kernel_source,
            reference_model=task.reference,
            get_inputs=task.get_inputs
        )
        
        if not eval_result.compiles:
            # Feed compilation error back to writer
            writer.add_feedback(eval_result.compile_error)
            continue
        
        if not eval_result.correct:
            writer.add_feedback(eval_result.correctness_error)
            continue
        
        # 4. Insert into MAP-Elites archive
        archive.try_insert(
            solution=kernel_source,
            fitness=-eval_result.execution_time_ms,  # minimize time
            descriptors=[eval_result.occupancy, eval_result.shared_mem_kb]
        )
        
        # 5. Update island with new profiler feedback
        island.record_profiler_output(eval_result.ncu_metrics)
```

---

## 6. The open-source landscape is now rich enough to build this

### Direct kernel generation systems

**KernelBench** (Stanford, ICML 2025) at `github.com/ScalingIntelligence/KernelBench` is the standard benchmark with 250 tasks across 4 difficulty levels. It includes the evaluation harness, reference PyTorch implementations, and the `fast_p` metric. Any system should target this benchmark. **KernelBench v2** at `github.com/Lossfunk/KernelBench-v2` adds unified Triton/CUDA evaluation with 15+ categorized error classes.

**Meta KernelEvolve** (arXiv:2512.23236, Dec 2025) is the strongest system to date — achieving **100% pass rate on all 250 KernelBench problems** with **1.25× to 17× speedups** on production recommendation models. It uses tree-search with selection policy and is deployed serving billions of users daily. While not open-source, its architecture is well-documented.

**CudaForge** (arXiv:2511.01884) is the closest open paper to Forge's architecture — a training-free multi-agent Coder+Judge workflow integrating Nsight Compute metrics for hardware feedback. Achieves **97.6% correctness and 1.68× average speedup** at ~$0.30/kernel.

**GEAK** (AMD, arXiv:2507.23194) is notable as the only system where **both the agent implementation AND evaluation framework are open-sourced**. It targets AMD GPUs but the architecture transfers to NVIDIA.

### Evolutionary search implementations

**OpenEvolve** at `github.com/algorithmicsuperintelligence/openevolve` implements the full AlphaEvolve architecture with configurable MAP-Elites islands, LLM ensembles, and multi-objective support. This is the most production-ready starting point for the evolutionary layer. A practical guide by Manoj Rao applied OpenEvolve specifically to matrix multiplication kernel optimization and won a GPU Mode hackathon.

**FunSearch community implementations** provide simpler starting points. The most complete is `github.com/kitft/funsearch` with multi-model support (Claude, GPT-4o, Gemini, DeepSeek), Docker sandboxing, WandB logging, and adaptive sampling.

### Specialized models and datasets

**KernelLLM** (8B, `facebook/KernelLLM`) is the only open-weight model fine-tuned for kernel generation. **Kevin-32B** (`cognition-ai/Kevin-32B`) is the first model trained with multi-turn RL for CUDA kernels. **Sakana AI's AI CUDA Engineer Archive** provides ~30,000 CUDA kernels under CC-By-4.0 for training data. **KernelGYM** (`github.com/hkust-nlp/KernelGYM`) provides a distributed GPU RL training environment with fault-isolated kernel evaluation.

### Production kernel libraries as reference

**ThunderKittens** (`github.com/HazyResearch/ThunderKittens`, 3.1K stars) provides C++ embedded DSL kernels that match cuBLAS on GEMM and FlashAttention-3 on attention. **Liger Kernel** (`github.com/linkedin/Liger-Kernel`) provides production Triton kernels for LLM training with +20% throughput and -60% memory. Both serve as reference targets — generated kernels should approach or exceed their performance. **Helion** (`github.com/pytorch/helion`) from Meta/PyTorch provides a higher-level Python DSL that compiles to Triton and serves as an alternative target language for LLM generation.

---

## 7. Step-by-step implementation roadmap

### Phase 1: Single writer-judge pair (Week 1–2)

**Goal**: Get one LLM generating kernels, compiling them, and verifying correctness.

1. **Set up KernelBench** — clone the repo, run the evaluation harness on 10 Level-1 tasks to understand the format
2. **Deploy a model** — start with KernelLLM (8B) via vLLM: `vllm serve facebook/KernelLLM --dtype float16`
3. **Build the compilation pipeline** — implement `compile_cuda_kernel()` using `load_inline` and `compile_triton_kernel()` using temp-file import
4. **Build the correctness checker** — compare against PyTorch reference with `torch.testing.assert_close()`
5. **Build the benchmarking harness** — use `triton.testing.do_bench()` for timing
6. **Wire it together** — single synchronous loop: prompt LLM → compile → test → benchmark → log results
7. **Measure baseline**: what pass rate and speedup does a single model achieve on KernelBench Level 1?

### Phase 2: Iterative refinement with feedback (Week 3–4)

**Goal**: Close the feedback loop so the agent improves across multiple generations.

1. **Add error feedback** — when compilation fails, append the error message to the next prompt; when correctness fails, append the diff between expected and actual outputs
2. **Add profiler feedback** — run `ncu` on successful kernels, extract key metrics, include them in the prompt
3. **Implement multi-turn conversation** — give the writer 5–10 turns to iteratively improve a kernel, as NVIDIA's DeepSeek-R1 workflow demonstrates this improves Level-1 from 12% → 43%
4. **Add sandboxing** — wrap evaluation in Docker containers with timeouts
5. **Add reward-hacking detection** — check that generated kernels don't simply call PyTorch functions, don't wrap in try-except fallbacks, and produce correct results on unseen input shapes

### Phase 3: Parallel swarm with MAP-Elites (Week 5–7)

**Goal**: Scale to multiple writer-judge pairs with evolutionary search.

1. **Set up Ray** — create a Ray cluster with GPU workers for evaluation and CPU workers for agent coordination
2. **Implement MAP-Elites archive** — use pyribs or a custom 4×4 grid (occupancy × shared memory). Define fitness as negative execution time.
3. **Implement best-shot prompting** — sample 3 kernels from the archive, sort by fitness, concatenate into context
4. **Scale to 8 pairs** — deploy 8 concurrent writer-judge actors, all feeding into one shared archive
5. **Add the island model** — split into 2 islands of 4 pairs each, implement FunSearch-style culling every 50 generations
6. **Benchmark against Phase 2** — measure QD-score (total fitness across archive) and best-kernel speedup

### Phase 4: Full 64-agent swarm (Week 8–10)

**Goal**: Scale to 32 writer-judge pairs across 4 islands with production-grade infrastructure.

1. **Scale LLM serving** — deploy vLLM with `--data-parallel-size 8` and prefix caching; monitor throughput and latency
2. **Scale to 32 pairs / 4 islands** — use different LLM configurations per island (varying temperature, model, or mutation strategy)
3. **Add mixed-model strategy** — use KernelLLM for fast exploration on 3 islands and a reasoning model (DeepSeek R1 or Kevin-32B) on the 4th island for breakthrough solutions
4. **Implement crossover** — LMX-style: show 2 parent kernels from different niches, ask LLM to combine their best features
5. **Add Triton as alternative target** — generate both CUDA and Triton variants; keep whichever is faster
6. **Benchmark on full KernelBench** — target all 250 tasks across Levels 1–3

### Phase 5: Production hardening (Week 11–12)

**Goal**: Handle edge cases, improve reliability, and optimize costs.

1. **Add Pattern RAG** — Forge uses a database of **1,711 CUTLASS patterns + 113 Triton patterns** for retrieval-augmented generation. Build a vector database of kernel patterns from ThunderKittens, Liger Kernel, and CUTLASS examples.
2. **Implement adaptive resource allocation** — devote more agent-hours to harder tasks, skip tasks where the current best is already near theoretical peak
3. **Add support for KernelBench Level 3** (full architectures) — requires fusing multiple operators, not just optimizing single kernels
4. **Implement persistent caching** — store all compiled kernels indexed by operation signature + hardware for reuse
5. **Cost monitoring** — track tokens consumed, GPU-hours for evaluation, and speedup achieved per dollar spent

### Key metrics to track throughout

- **fast_p at p=1**: fraction of tasks where generated kernels are both correct AND faster than PyTorch eager
- **QD-score**: sum of all fitness values across MAP-Elites archive cells (measures both quality and diversity)
- **Coverage**: fraction of archive cells filled (measures exploration)
- **Tokens per kernel**: total LLM tokens consumed per successful kernel (optimization target)
- **Wall-clock time per task**: end-to-end time from task submission to best kernel found

---

## Conclusion

The convergence of specialized kernel-generation LLMs (KernelLLM, Kevin-32B), evolutionary search frameworks (OpenEvolve, pyribs), standardized benchmarks (KernelBench), and high-throughput serving infrastructure (vLLM, SGLang) makes this system implementable today without training any new models. The most important architectural insight is that **the evolutionary search layer matters more than the choice of LLM** — Meta's KernelEvolve achieves 100% pass rates because of its tree-search and fitness-driven selection, not because of any single model's capabilities. Start with a single writer-judge pair on KernelBench Level 1 tasks, close the feedback loop with profiler output, then scale horizontally by adding islands and pairs. The data shows that multi-agent approaches consistently outperform single-agent baselines (Astra: 1.32× vs 1.08×; CudaForge: 1.68× average speedup), and that iterative refinement with execution feedback is the single highest-leverage technique (DeepSeek R1: 12% → 43% on Level 1 with 10 turns). The system is not merely theoretically interesting — it is being deployed in production at Meta serving billions of users, with 1.25× to 17× speedups on real recommendation workloads.