# Contributing

1. `make install`, then create a branch.
2. Add or change a tool in `rca/tools.py` with a Pydantic input model and evidence records.
3. If you add a root-cause type, add a rule in `rca/analysis.py`, injection in the generator and evaluation cases.
4. `make lint test eval` must pass; paste changed evaluation metrics in the pull request.
