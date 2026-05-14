from __future__ import annotations


class DeprecatedLocalAuthCache(RuntimeError):
    pass


async def ensure_local_auth_table(_db) -> None:
    return None


async def upsert_local_auth_cache(*_args, **_kwargs):
    raise DeprecatedLocalAuthCache("local_auth_cache belonged to the old refresh-token license flow")


async def get_local_auth_cache(_db):
    return None


async def clear_local_auth_cache(_db) -> None:
    return None


def cache_allows_offline_use(_cache) -> bool:
    return False
