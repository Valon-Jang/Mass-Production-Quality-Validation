"""Local actor and role boundaries.

The product currently has one opt-in local user. "Owner" describes that actor;
it is not a sixth authorization role. System work and AI explanations use
separate principals so their audit records cannot be mistaken for user actions.
"""

from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    REVIEWER = "REVIEWER"
    VIEWER = "VIEWER"
    SYSTEM = "SYSTEM"
    AI_PROVIDER = "AI_PROVIDER"


class ActorKind(StrEnum):
    LOCAL_OWNER = "LOCAL_OWNER"
    SYSTEM = "SYSTEM"
    AI_PROVIDER = "AI_PROVIDER"


@dataclass(frozen=True, slots=True)
class Actor:
    actor_id: str
    kind: ActorKind
    roles: frozenset[Role]

    def __post_init__(self) -> None:
        if not self.actor_id.strip():
            raise ValueError("actor_id must not be blank")
        if not self.roles:
            raise ValueError("an actor must have at least one role")

        allowed_roles = {
            ActorKind.LOCAL_OWNER: frozenset({Role.ADMIN, Role.REVIEWER, Role.VIEWER}),
            ActorKind.SYSTEM: frozenset({Role.SYSTEM}),
            ActorKind.AI_PROVIDER: frozenset({Role.AI_PROVIDER}),
        }
        allowed = allowed_roles[self.kind]
        roles_are_valid = (
            bool(self.roles) and self.roles.issubset(allowed)
            if self.kind == ActorKind.LOCAL_OWNER
            else self.roles == allowed
        )
        if not roles_are_valid:
            raise ValueError(f"roles do not match actor kind {self.kind.value}")

    def has_role(self, role: Role) -> bool:
        return role in self.roles


LOCAL_OWNER = Actor(
    actor_id="local-owner",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.ADMIN, Role.REVIEWER, Role.VIEWER}),
)
SYSTEM_ACTOR = Actor(
    actor_id="mass-production-quality-validation-system",
    kind=ActorKind.SYSTEM,
    roles=frozenset({Role.SYSTEM}),
)
AI_PROVIDER_ACTOR = Actor(
    actor_id="mass-production-quality-validation-ai-provider",
    kind=ActorKind.AI_PROVIDER,
    roles=frozenset({Role.AI_PROVIDER}),
)
