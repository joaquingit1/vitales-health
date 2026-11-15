# mypy: disable-error-code="type-abstract"
"""E17 optional providers, registered without starting the model or reading artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol, cast

from crew_health.kernel.settings import HubSettings, SettingSpec
from crew_health.model.adapters.launch import LaunchBuilder
from crew_health.model.adapters.llama_client import ClientLimits, LlamaClient
from crew_health.model.adapters.manifest_store import ManifestStore
from crew_health.model.adapters.prompt_assets import FilePromptAssets
from crew_health.model.adapters.runtime_service import RuntimeService
from crew_health.model.adapters.stub import StubResponder, validate_script
from crew_health.model.adapters.subprocess_supervisor import DevelopmentSupervisor
from crew_health.model.adapters.token_counter import RuntimeTokenCounter
from crew_health.model.application.ports import (
    CancelToken,
    ContextSettings,
    ContextSnapshot,
    ControllerSettings,
    Deadline,
    EmbeddingPin,
    InferencePort,
    LaunchSpec,
    ModelArtifactTrust,
    ModelInstallation,
    ModelLaunchSpecPort,
    ModelRelease,
    ModelReleaseReader,
    ModelReleaseRef,
    ModelRuntimePort,
    Phase,
    PromptBundle,
    PromptPort,
    RetrievalSettings,
    RuntimeProbe,
    RuntimeSettings,
    StepProgress,
    StepRequest,
    StepResult,
    StepUnavailable,
    StubInstallation,
    SupervisorControl,
    ToolCatalog,
    TranscriptItem,
)
from crew_health.model.application.prompt_builder import PromptBuilder
from crew_health.model.domain.release import ModelRefused
from crew_health.model.settings import specifications


class Resolver(Protocol):
    def require[P](self, port: type[P]) -> P: ...
    def optional[P](self, port: type[P]) -> P | None: ...


class Registry(Protocol):
    def provide[P](
        self, port: type[P], factory: Callable[[Resolver], P], *, requires: tuple[type[object], ...] = ()
    ) -> None: ...
    def bind(self, name: str, fn: Callable[[Resolver], None]) -> None: ...
    def setting(self, spec: SettingSpec) -> None: ...


class ModelServices:
    def __init__(self) -> None:
        self.stub_installation: StubInstallation | None = None
        self.stub: StubResponder | None = None
        self.stub_release_id: str | None = None
        self.stub_fingerprint: str | None = None
        self.store: ManifestStore | None = None
        self.runtime: RuntimeService | None = None
        self.launch: LaunchBuilder | None = None
        self.limits: ClientLimits | None = None

    def _store(self) -> ManifestStore:
        if self.store is None:
            raise ModelRefused("release_not_found")
        return self.store

    def active(self) -> ModelRelease | None:
        return self.store.active() if self.store is not None else None

    def get(self, release_id: str) -> ModelRelease:
        return self._store().get(release_id)

    def load(self, release_id: str) -> ModelReleaseRef:
        return self._store().load(release_id)

    def tool_catalog(self, model_release_id: str) -> ToolCatalog:
        return self._store().tool_catalog(model_release_id)

    def retrieval_settings(self, model_release_id: str) -> RetrievalSettings:
        return self._store().retrieval_settings(model_release_id)

    def context_settings(self, model_release_id: str) -> ContextSettings:
        return self._store().context_settings(model_release_id)

    def runtime_settings(self, model_release_id: str) -> RuntimeSettings:
        return self._store().runtime_settings(model_release_id)

    def controller_settings(self, model_release_id: str) -> ControllerSettings:
        return self._store().controller_settings(model_release_id)

    def embedding_pin(self) -> EmbeddingPin:
        return self._store().embedding_pin()

    def start(self, release_id: str) -> None:
        if self.stub is not None or self.runtime is not None and self.runtime.release_id is not None:
            raise ModelRefused("runtime_already_started")
        release = self.get(release_id)
        if release.mode == "stub":
            installed = self.stub_installation
            if installed is None:
                raise ModelRefused("stub_script_unavailable")
            if hashlib.sha256(installed.script_json).hexdigest() != release.document.get(
                "stub_script_sha256"
            ):
                raise ModelRefused("stub_script_unpinned")
            document = validate_script(
                installed.script_json,
                self._store().read_json(self._store().root / "schemas/stub-script.schema.json"),
                max_bytes=self._store().max_bytes,
            )
            self.stub = StubResponder(
                document,
                profile=self._store().profile,
                scenario=installed.scenario,
                conversation_ordinal=installed.conversation_ordinal,
            )
            self.stub_release_id = release_id
            self.stub_fingerprint = self._store().fingerprint(release_id)
            return
        if self.runtime is None:
            raise ModelRefused("runtime_unavailable")
        self.runtime.start(release_id)

    def stop(self) -> None:
        self.stub = None
        self.stub_release_id = None
        self.stub_fingerprint = None
        if self.runtime is not None:
            self.runtime.stop()

    def probe(self, *, timeout_s: float) -> RuntimeProbe:
        if self.stub is not None:
            if (
                self.stub_release_id is None
                or self._store().fingerprint(self.stub_release_id) != self.stub_fingerprint
            ):
                return RuntimeProbe(False, False, False, "release_changed")
            return RuntimeProbe(True, True, True, "stub")
        return (
            self.runtime.probe(timeout_s=timeout_s)
            if self.runtime
            else RuntimeProbe(False, False, False, "model_unavailable")
        )

    def launch_spec(self, release_id: str) -> LaunchSpec:
        if self.launch is None:
            raise ModelRefused("runtime_unavailable")
        return self.launch.launch_spec(release_id)

    def _counter(self) -> RuntimeTokenCounter:
        if self.runtime is None or not self.runtime.key or self.limits is None:
            raise ModelRefused("runtime_unavailable")
        return RuntimeTokenCounter(
            port=self.runtime.port,
            key=self.runtime.key,
            max_bytes=self.limits.max_bytes,
            timeout_s=self.limits.connect_timeout_s,
        )

    def build(
        self, release_id: str, phase: Phase, context: ContextSnapshot, transcript: Sequence[TranscriptItem]
    ) -> PromptBundle:
        if self.stub_release_id == release_id and self.stub_installation is not None:
            if self._store().fingerprint(release_id) != self.stub_fingerprint:
                raise ModelRefused("release_changed")
            return PromptBuilder(
                self._store(), FilePromptAssets(self._store()), self.stub_installation.tokens
            ).build(release_id, phase, context, transcript)
        if self.limits is None:
            raise ModelRefused("runtime_unavailable")
        checked = self.probe(timeout_s=self.limits.connect_timeout_s)
        if not (checked.healthy and checked.identity_ok and checked.adapters_empty):
            raise ModelRefused("runtime_unavailable")
        return PromptBuilder(self._store(), FilePromptAssets(self._store()), self._counter()).build(
            release_id, phase, context, transcript
        )

    def run_step(
        self,
        request: StepRequest,
        *,
        deadline: Deadline,
        cancel: CancelToken,
        progress: Callable[[StepProgress], None],
    ) -> StepResult:
        if self.stub is not None and self.stub_release_id == request.release_id:
            if self._store().fingerprint(request.release_id) != self.stub_fingerprint:
                return StepUnavailable("release_changed")
            return self.stub.run_step(request, deadline=deadline, cancel=cancel, progress=progress)
        if self.runtime is None or self.limits is None or self.runtime.release_id != request.release_id:
            return StepUnavailable("model_unavailable")
        checked = self.probe(timeout_s=self.limits.connect_timeout_s)
        if not (checked.healthy and checked.identity_ok and checked.adapters_empty):
            return StepUnavailable("model_identity_mismatch")
        client = LlamaClient(
            port=self.runtime.port, api_key=self.runtime.key, tokens=self._counter(), limits=self.limits
        )
        return client.run_step(request, deadline=deadline, cancel=cancel, progress=progress)


def register(registry: Registry) -> None:
    services = ModelServices()
    for spec in specifications():
        registry.setting(spec)
    for port in (ModelReleaseReader, PromptPort, InferencePort, ModelRuntimePort, ModelLaunchSpecPort):
        registry.provide(cast(type[object], port), lambda resolver: services)
    registry.provide(ModelServices, lambda resolver: services)

    def bind(resolver: Resolver) -> None:
        installation = resolver.optional(ModelInstallation)
        if installation is None:
            return
        settings = resolver.require(HubSettings)
        store = ManifestStore(
            Path(installation.artifact_root),
            Path(installation.data_dir),
            profile=installation.profile,
            max_bytes=settings.integer("model.max_bytes"),
        )
        services.store = store
        services.stub_installation = resolver.optional(StubInstallation)
        help_text = (store.root / "build/llama-server-help.txt").read_text(encoding="utf-8")
        services.launch = LaunchBuilder(
            store,
            binary=Path(installation.binary),
            port=installation.port,
            captured_help=help_text,
            memory_budget_bytes=settings.integer("model.memory_budget_bytes"),
            cpu_budget=settings.integer("model.cpu_budget"),
            nice=settings.integer("model.nice"),
        )
        services.limits = ClientLimits(
            settings.integer("model.max_bytes"),
            settings.integer("model.max_chunks"),
            settings.integer("model.max_tools"),
            settings.integer("model.connect_timeout_ms") / 1000,
            settings.integer("model.cancel_poll_ms") / 1000,
        )
        supervisor = resolver.optional(SupervisorControl)
        if supervisor is None and installation.profile in ("demo", "eval"):
            supervisor = DevelopmentSupervisor(
                profile=installation.profile, stop_grace_ms=settings.integer("model.stop_grace_ms")
            )
        if supervisor is not None:
            services.runtime = RuntimeService(
                store,
                services.launch,
                supervisor,
                port=installation.port,
                max_bytes=services.limits.max_bytes,
                trust=resolver.optional(ModelArtifactTrust),
            )

    registry.bind("model", bind)
