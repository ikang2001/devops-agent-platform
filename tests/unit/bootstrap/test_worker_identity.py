from devops_agent_platform.bootstrap.worker_identity import (
    derive_suffixed_id,
    derive_worker_id,
)


def test_derive_suffixed_id_keeps_short_identity_readable() -> None:
    """短身份保持原样可读，方便日志和 Broker 侧排查。"""
    assert derive_suffixed_id("rca-consumer", "dlq") == "rca-consumer-dlq"


def test_derive_suffixed_id_hashes_long_identity() -> None:
    """长身份不能静默截断，否则共享长前缀的部署会撞名。"""
    base_id = "rca-" + "a" * 124

    derived = derive_suffixed_id(base_id, "dlq")

    assert len(derived) == 128
    assert derived.endswith("-dlq")
    assert derived != f"{base_id[:119]}-dlq"


def test_derive_suffixed_id_uses_full_candidate_for_digest() -> None:
    """哈希必须覆盖完整候选值，不能只看被保留的前缀。"""
    first = "consumer-" + "a" * 130 + "1"
    second = "consumer-" + "a" * 130 + "2"

    assert derive_suffixed_id(first, "dlq") != derive_suffixed_id(
        second,
        "dlq",
    )


def test_derive_worker_id_reuses_generic_suffixed_identity_rule() -> None:
    """Worker ID 和其它派生身份共享同一套长度与哈希规则。"""
    client_id = "ticket-" + "b" * 121

    assert derive_worker_id(client_id) == derive_suffixed_id(
        client_id,
        "worker",
    )
