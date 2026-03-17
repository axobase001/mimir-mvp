# Brain Core — Proprietary Components

Two files in this directory are not included in the open-source release:

- **`sec_matrix.py`** — Staleness-Error Correlation (SEC) matrix implementation
- **`belief_graph.py`** — Belief graph with Bayesian confidence updates and dependency propagation

These implement the core research from the Noogenesis project (arXiv:2603.09476).

## Interface

Both modules expose standard Python classes. See `sec_matrix_interface.py` and `belief_graph_interface.py` for the full API signatures. The rest of the codebase imports from these modules — you can provide your own implementation following the interface contracts.

## SEC Matrix

```python
class SECMatrix:
    def update(self, observed_clusters, all_clusters, pe, cycle) -> None
    def get_c_value(self, cluster) -> float
    def filter_action(self, cluster, cycle) -> bool  # True=allow, False=reject
    def get_top_clusters(self, n) -> list[tuple[str, float]]
    def get_negative_clusters(self) -> list[tuple[str, float]]
    def to_dict(self) -> dict
    def from_dict(cls, data, config) -> SECMatrix
```

## Belief Graph

```python
class BeliefGraph:
    def add_belief(self, belief) -> str
    def update_belief(self, belief_id, new_confidence, pe, cycle) -> None
    def add_dependency(self, from_id, to_id, weight) -> None
    def propagate_update(self, updated_id) -> list[str]
    def decay_unverified(self, current_cycle) -> list[str]
    def prune(self) -> list[str]
    def get_beliefs_by_tag(self, tag) -> list[Belief]
    def get_high_pe_beliefs(self, threshold, min_persistence) -> list[Belief]
    def get_stale_beliefs(self, current_cycle, staleness_threshold) -> list[Belief]
    def to_dict(self) -> dict
    def from_dict(cls, data, config) -> BeliefGraph
```
