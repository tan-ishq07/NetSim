"""NetSim tutor-AI data pipeline.

This package builds a *self-labeled* supervised fine-tuning dataset for the
NetSim tutor model. The NetSim engine is the oracle: we take a working lab,
inject a single, known fault, run the engine to observe the resulting symptoms
(failed pings, down interfaces, analyzer findings), and pair those symptoms with
the diagnosis + exact IOS fix that the fault injector knows it created.

Nothing here is needed at request time - it is a build-time tool that emits a
JSONL dataset (see `generate.py`). It depends only on the standard library and
`app.engine`, so it runs anywhere the engine runs (and in CI)."""

from .symptoms import collect_symptoms
from .dataset import build_dataset, example_from_fault

__all__ = ["collect_symptoms", "build_dataset", "example_from_fault"]
