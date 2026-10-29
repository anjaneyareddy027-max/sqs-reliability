# notebooks/

Optional scratch space for exploring results interactively. Final numbers and
figures always come from the scripts (`analysis/stats_tests.py`,
`analysis/plot_results.py`) so they can be regenerated from the manifests.

Quick start in a notebook:

```python
import sys; sys.path.insert(0, "..")
from load_results import load_runs
df = load_runs("../../results")
df.groupby(["campaign", "visibility_timeout"])["recovery_time_s"].describe()
```
