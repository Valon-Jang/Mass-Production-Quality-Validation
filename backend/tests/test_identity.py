import pytest

from app.domain.identity import (
    AI_PROVIDER_ACTOR,
    LOCAL_OWNER,
    SYSTEM_ACTOR,
    Actor,
    ActorKind,
    Role,
)


@pytest.mark.required_test_id("DQ-P0-IDENTITY-001")
def test_local_owner_has_only_human_roles() -> None:
    assert LOCAL_OWNER.roles == frozenset({Role.ADMIN, Role.REVIEWER, Role.VIEWER})
    assert not LOCAL_OWNER.has_role(Role.SYSTEM)
    assert not LOCAL_OWNER.has_role(Role.AI_PROVIDER)


def test_non_human_principals_are_separate_and_least_privileged() -> None:
    assert SYSTEM_ACTOR.actor_id != LOCAL_OWNER.actor_id
    assert AI_PROVIDER_ACTOR.actor_id != LOCAL_OWNER.actor_id
    assert SYSTEM_ACTOR.roles == frozenset({Role.SYSTEM})
    assert AI_PROVIDER_ACTOR.roles == frozenset({Role.AI_PROVIDER})


def test_actor_kind_cannot_be_given_another_kinds_roles() -> None:
    with pytest.raises(ValueError, match="roles do not match actor kind"):
        Actor(
            actor_id="invalid-system",
            kind=ActorKind.SYSTEM,
            roles=frozenset({Role.ADMIN}),
        )
