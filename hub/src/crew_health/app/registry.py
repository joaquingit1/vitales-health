from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol, TypeGuard, get_protocol_members, is_protocol

from fastapi import APIRouter

from crew_health.contracts_gen.enums import EventType

if TYPE_CHECKING:
    from crew_health.kernel.audit import AuditPolicy
    from crew_health.kernel.commands import CommandResumer
    from crew_health.kernel.config import ActivationGuard, ReleaseCheck
    from crew_health.kernel.evaluation import EvaluatorRegistration
    from crew_health.kernel.private import PrivateReferenceWriter
    from crew_health.kernel.replication import ProjectionRegistration, ReplicatedTable
    from crew_health.kernel.scheduler import ScheduledJob
    from crew_health.kernel.settings import SettingSpec
    from crew_health.kernel.tasks import TaskRegistration
    from crew_health.kernel.uow import (
        AudienceRule,
        DomainEvent,
        LocalProjectionRegistration,
        StoredEvent,
        UnitOfWork,
    )


class StartupPhase(IntEnum):
    STORAGE = 10
    CONFIG = 20
    CLOCK = 30
    STATE_SWEEP = 40
    INGEST = 50
    SERVE = 60


class Resolver(Protocol):
    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class ModuleRegistry(Protocol):
    def router(self, router: APIRouter) -> None: ...
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def subscribe[E: DomainEvent](
        self, event_type: type[E], handler: Callable[[UnitOfWork, E], None], *, name: str
    ) -> None: ...
    def audience_rule(self, name: str, rule: AudienceRule) -> None: ...
    def projection(self, registration: ProjectionRegistration) -> None: ...
    def local_projection(self, registration: LocalProjectionRegistration) -> None: ...
    def task(self, registration: TaskRegistration) -> None: ...
    def evaluator(self, registration: EvaluatorRegistration) -> None: ...
    def private_records(self, record_kind: str, writer: PrivateReferenceWriter) -> None: ...
    def internal_command(self, operation: str) -> None: ...
    def command_resumer(self, name: str, resumer: CommandResumer) -> None: ...
    def durable_subscriber(
        self, subscriber: str, types: frozenset[EventType], handler: Callable[[UnitOfWork, StoredEvent], None]
    ) -> None: ...
    def loader_check(self, check: ReleaseCheck) -> None: ...
    def activation_guard(self, guard: ActivationGuard) -> None: ...
    def scheduler_handler(self, name: str, fn: Callable[[UnitOfWork, ScheduledJob], None]) -> None: ...
    def on_startup(self, phase: StartupPhase, fn: Callable[[], None]) -> None: ...
    def id_prefix(self, *, record_type: str, prefix: str) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...
    def audit_policy(self, policy: AuditPolicy) -> None: ...
    def replicated_table(self, table: ReplicatedTable) -> None: ...


class DeclarationRegistry:
    """Bounded declarations for component tests; freeze never implies a served release."""

    def __init__(self, *, max_ports: int, max_hooks: int) -> None:
        if not 1 <= max_ports <= 1024 or not 1 <= max_hooks <= 1024:
            raise ValueError("registry bounds outside E01 11")
        self.max_ports = max_ports
        self.max_hooks = max_hooks
        self.providers: dict[type[object], tuple[Callable[[Resolver], object], tuple[type[object], ...]]] = {}
        self.bindings: dict[str, Callable[[Resolver], None]] = {}
        self.declarations: dict[str, dict[object, object]] = {}
        self.frozen = False
        self.resolving = False

    def _add(self, category: str, key: object, value: object) -> None:
        if self.frozen or self.resolving:
            raise ValueError("registry declarations are closed")
        bucket = self.declarations.get(category, {})
        total = sum(len(v) for v in self.declarations.values())
        if key in bucket or total >= self.max_hooks:
            raise ValueError("duplicate registration or registry.max_hooks exceeded")
        bucket[key] = value
        self.declarations[category] = bucket

    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None:
        if port in self.providers or len(self.providers) >= self.max_ports:
            raise ValueError("duplicate port or registry.max_ports exceeded")
        self._add("ports", port, requires)
        self.providers[port] = (factory, requires)

    def replace[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None:
        """A served profile's composition (app/station.py) swaps the provider a module declared for `port`. Only an
        already declared port can be replaced, and only while declarations are open."""
        if self.frozen or self.resolving:
            raise ValueError("registry declarations are closed")
        if port not in self.providers:
            raise ValueError("only a declared port can be replaced")
        self.declarations["ports"][port] = requires
        self.providers[port] = (factory, requires)

    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None:
        self._add("bindings", name, fn)
        self.bindings[name] = fn

    def resolve(self, *, before_bindings: Callable[[Resolver], None] | None = None) -> Resolver:
        """Construct every provider, run `before_bindings` (assembly's clock guard, so a binding's first write is
        already guarded), then invoke the named bindings in sorted order."""
        if self.frozen or self.resolving:
            raise ValueError("registry already resolved")
        self.resolving = True
        values: dict[type[object], object] = {}
        pending = dict(self.providers)
        for _ in range(self.max_ports):
            if not pending:
                break
            ready = sorted(
                (p for p, (_, deps) in pending.items() if all(d in values for d in deps)),
                key=lambda p: (p.__module__, p.__qualname__),
            )
            if not ready:
                raise ValueError("missing required provider or provider cycle")
            for port in ready:
                factory, dependencies = pending.pop(port)
                values[port] = factory(_Resolver(values, frozenset(dependencies), late=False))
        if pending:
            raise ValueError("registry.max_ports exceeded")
        resolver = _Resolver(values, frozenset(values), late=True)
        if before_bindings is not None:
            before_bindings(resolver)
        for name in sorted(self.bindings):
            self.bindings[name](resolver)
        return resolver

    def freeze(self, *, required_loader_checks: frozenset[str]) -> None:
        checks = self.declarations.get("loader_checks", {})
        if not required_loader_checks <= checks.keys():
            raise ValueError("mandatory loader contributor missing")
        self.frozen = True

    def router(self, router: APIRouter) -> None:
        self._add("routers", id(router), router)

    def subscribe[E: DomainEvent](
        self, event_type: type[E], handler: Callable[[UnitOfWork, E], None], *, name: str
    ) -> None:
        self._add("subscriptions", name, (event_type, handler))

    def audience_rule(self, name: str, rule: AudienceRule) -> None:
        self._add("audiences", name, rule)

    def projection(self, registration: ProjectionRegistration) -> None:
        import hashlib

        if hashlib.sha256(registration.schema_json).hexdigest() != registration.schema_sha256:
            raise ValueError("projection schema hash mismatch")
        self._add("projections", registration.key, registration)

    def local_projection(self, registration: LocalProjectionRegistration) -> None:
        self._add("local_projections", registration.key, registration)

    def task(self, registration: TaskRegistration) -> None:
        if not registration.version or (
            registration.public_kind is not None and registration.result_projector is None
        ):
            raise ValueError("task version or public result projector missing")
        self._add("tasks", registration.name, registration)

    def evaluator(self, registration: EvaluatorRegistration) -> None:
        if not registration.version:
            raise ValueError("evaluator version missing")
        self._add("evaluators", (registration.name, registration.version), registration)

    def private_records(self, record_kind: str, writer: PrivateReferenceWriter) -> None:
        self._add("private_writers", record_kind, writer)

    def internal_command(self, operation: str) -> None:
        self._add("internal_commands", operation, operation)

    def command_resumer(self, name: str, resumer: CommandResumer) -> None:
        self._add("resumers", name, resumer)

    def durable_subscriber(
        self, subscriber: str, types: frozenset[EventType], handler: Callable[[UnitOfWork, StoredEvent], None]
    ) -> None:
        self._add("durable_subscribers", subscriber, (types, handler))

    def loader_check(self, check: ReleaseCheck) -> None:
        self._add("loader_checks", check.check_id, check)

    def activation_guard(self, guard: ActivationGuard) -> None:
        self._add("activation_guards", guard.check_id, guard)

    def scheduler_handler(self, name: str, fn: Callable[[UnitOfWork, ScheduledJob], None]) -> None:
        self._add("scheduler_handlers", name, fn)

    def on_startup(self, phase: StartupPhase, fn: Callable[[], None]) -> None:
        self._add("startup", (phase, fn.__module__, fn.__qualname__), fn)

    def id_prefix(self, *, record_type: str, prefix: str) -> None:
        self._add("prefixes", prefix, record_type)

    def setting(self, spec: SettingSpec) -> None:
        if not spec.basis or (spec.kind in ("integer", "quantity") and spec.maximum is None):
            raise ValueError("setting has no provenance or finite ceiling")
        self._add("settings", spec.key, spec)

    def audit_policy(self, policy: AuditPolicy) -> None:
        """Register the policy for one audit event type the module writes; the assembly installs it in storage."""
        self._add("audit_policies", policy.event_type, policy)

    def replicated_table(self, table: ReplicatedTable) -> None:
        """Register a module-owned replicated table and the leaf fields its projection may carry."""
        if not table.table.isidentifier() or not table.key_column.isidentifier() or not table.record_type:
            raise ValueError("replicated table identity invalid")
        if (table.projection is None) != (not table.allowed_fields) or any(
            not pointer.startswith("/") or len(pointer) < 2 for pointer in table.allowed_fields
        ):
            raise ValueError("projection allowlist needs a projection and JSON pointer leaves")
        self._add("replicated_tables", table.table, table)


def _conforms[P](value: object, port: type[P]) -> TypeGuard[P]:
    if is_protocol(port):
        return all(hasattr(value, member) for member in get_protocol_members(port))
    return isinstance(value, port)


class _Resolver:
    def __init__(
        self, values: Mapping[type[object], object], allowed: frozenset[type[object]], *, late: bool
    ) -> None:
        self.values = dict(values)
        self.allowed = allowed
        self.late = late

    def require[P](self, port: type[P]) -> P:
        if port not in self.allowed or port not in self.values:
            raise ValueError("missing or undeclared factory dependency")
        value = self.values[port]
        if not _conforms(value, port):
            raise ValueError("provider violates its port")
        return value

    def optional[P](self, port: type[P]) -> P | None:
        if not self.late:
            raise ValueError("optional lookup requires a late binding")
        if port not in self.values:
            return None
        return self.require(port)
