# Medes Experiment

This experiment test whether naturally occurring sharing is similar (close) to the memory savings reported in the Medes paper.

| BENCHMARKS | MEDES IDENTIFIED REDUNDANCY (%) | NATURALLY REALIZED SHARING (%) |
|---|---:|---:|
| Vanilla | 93% | 84.1% |
| LinAlg | 94% | 83.57% |
| ImagePro | 95% | 78.6% |
| VideoPro | 88% | 59.1% |
| MapReduce | 88% | 83.7% |
| HTTPServe | 95% | 68% |
| AuthEnc | 94% | 83.22% |
| FeatureGen | 93% | 48.32% |
| ModelServe | 94% | 55.83% |
| ModelTrain | 90% | 53.7% |

## NATURALLY REALIZED SHARING (%)

This sharing represents the amount of sharing of the 2nd container with 1st container of the same function. For this the progression of total memory of cgroup parent was tracked at the steps of creation of container which give marginal memory required for creating additional containers, this marginal memory required was subtracted from first container memory consumption, which is the first value of total memory of cgroup parent, to get sharing amount and then divided by the first container memory sharing percentage to get sharing percentage.

For e.g in case of Vanilla, total memory of cgroup parent when 1st container was created was:

**38.82 MB**

and, total memory of cgroup parent when 2nd container was created was:

**44.96 MB**

Therefore, marginal memory required for creation of 2nd container is:

**44.96 - 38.82 = 6.14 MB**

Therefore, sharing:

**38.82 - 6.14 MB = 32.68 MB**

Therefore, sharing of 2nd container with 1st container:

**32.68 / 38.82 = 84.1%**

## MEDES IDENTIFIED REDUNDANCY (%)

The values in this column are taken from Figure 1(a) of the Medes paper at the selected chunk granularity. Medes measures the memory redundancy between two sandboxes belonging to the same function. It checkpoints the memory state of the sandboxes using CRIU and uses Rabin fingerprinting to identify matching memory chunks. For two sandboxes A and B, the reported redundancy is the fraction of bytes in sandbox B that are also present in sandbox A. Figure 1(a) performs this experiment with ASLR disabled to estimate an upper bound on memory redundancy.

This measurement represents the potential redundancy present in the sandbox memory state.