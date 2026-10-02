from insights.redis import (
    github_etag_key,
    narrative_key,
    narrative_lock_key,
    rate_limit_key,
    rows_key,
    snapshot_key,
    sync_cooldown_key,
    sync_lock_key,
)


def test_redis_namespace_and_cache_identity():
    assert snapshot_key("s_abc") == "di:snap:s_abc"
    assert rows_key("s_abc") == "di:rows:s_abc"
    assert sync_lock_key("A/B") == "di:lock:sync:a/b"
    assert sync_cooldown_key("A/B") == "di:cooldown:sync:a/b"
    assert narrative_lock_key("s_abc", "manager", "en") == "di:lock:narr:s_abc:manager:en"
    assert rate_limit_key("127.0.0.1", 3) == "di:rl:127.0.0.1:3"
    assert narrative_key("s_abc", "manager", "en", "v1", "model", "hash").endswith(":v1:model:hash")
    assert github_etag_key("https://api.github.com/a?page=1") != github_etag_key(
        "https://api.github.com/a?page=2"
    )
