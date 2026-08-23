from collections import Counter
from pathlib import Path

from flamingo_bot.config import load_model_config
from flamingo_bot.ingest import removed_chunk_count, reuse_contract_for


def test_removed_chunk_count_preserves_duplicate_hash_multiplicity() -> None:
    active = {"same": 3, "removed": 2}
    current = Counter({"same": 1, "new": 4})

    assert removed_chunk_count(active, current) == 4


def test_reuse_contract_tracks_all_embedding_compatibility_versions() -> None:
    contract = reuse_contract_for(load_model_config(Path("openai.yaml")))

    assert contract.embedding_dimensions == 1024
    assert contract.parser_version
    assert contract.chunker_version
    assert contract.normalization_version
