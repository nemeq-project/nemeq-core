"""Dataclass models for the nemeq.io/v1alpha1 API.

D-11: the apiserver owns the object; massaru reconciles ``Epishu`` rather
than writing files directly.  These models are the Python-side contract
until the OpenAPI/resource schema lands in a separate commit.

Each resource has ``spec``, ``status`` and ``resource_version``.  Serialized
form uses ``resourceVersion`` (API spelling); dataclass fields are snake_case.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, ClassVar, get_type_hints

API_VERSION = "nemeq.io/v1alpha1"


class APIObject:
    """Mixin: spec/status/resourceVersion envelope."""

    api_version: ClassVar[str] = API_VERSION
    kind: ClassVar[str] = ""

    @property
    def rev(self) -> int:
        """API revision alias (resourceVersion)."""
        return int(self.resource_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "apiVersion": self.api_version,
            "kind": self.kind,
            "name": getattr(self, "name", ""),
            "uid": getattr(self, "uid", ""),
            "spec": asdict(self.spec),
            "status": asdict(self.status),
            "resourceVersion": int(self.resource_version),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "APIObject":
        hints = get_type_hints(cls)
        spec_type = hints["spec"]
        status_type = hints["status"]
        return cls(
            name=str(payload.get("name", "")),
            uid=str(payload.get("uid", "")),
            spec=spec_type(**payload.get("spec", {})),
            status=status_type(**payload.get("status", {})),
            resource_version=int(payload.get("resourceVersion", 0)),
        )


@dataclass
class ZiSpec:
    image: str
    digest: str
    manifest: dict[str, Any] = field(default_factory=dict)


@dataclass
class ZiStatus:
    phase: str = "Pending"
    verified: bool = False


@dataclass
class Zi(APIObject):
    kind: ClassVar[str] = "Zi"
    name: str
    spec: ZiSpec
    status: ZiStatus = field(default_factory=ZiStatus)
    resource_version: int = 0
    uid: str = ""


@dataclass
class SuSpec:
    image: str
    digest: str
    capabilities: list[str] = field(default_factory=list)
    endpoint_class: str = "default"
    manifest: dict[str, Any] = field(default_factory=dict)


@dataclass
class SuStatus:
    phase: str = "Pending"
    verified: bool = False


@dataclass
class Su(APIObject):
    kind: ClassVar[str] = "Su"
    name: str
    spec: SuSpec
    status: SuStatus = field(default_factory=SuStatus)
    resource_version: int = 0
    uid: str = ""


@dataclass
class EpishuSpec:
    zi_ref: str
    su_ref: str
    model: str = ""
    quotas: dict[str, Any] = field(default_factory=dict)
    kind: str = "awilu"
    read_only: bool = True


@dataclass
class EpishuStatus:
    phase: str = "Pending"
    node: str = ""
    session_ref: str | None = None
    conditions: list[dict[str, Any]] = field(default_factory=list)
    observed_generation: int = 0
    layout_path: str = ""
    zi_digest: str = ""
    su_digest: str = ""
    model: str = ""


@dataclass
class Epishu(APIObject):
    kind: ClassVar[str] = "Epishu"
    name: str
    spec: EpishuSpec
    status: EpishuStatus = field(default_factory=EpishuStatus)
    resource_version: int = 0
    uid: str = ""


@dataclass
class SessionSpec:
    epishu_ref: str
    principal: str
    client_id: str
    priority: int = 0
    budget: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionStatus:
    phase: str = "Queued"
    queue_pos: int = 0
    sync_token: str = ""
    epoch: int = 0


@dataclass
class Session(APIObject):
    kind: ClassVar[str] = "Session"
    name: str
    spec: SessionSpec
    status: SessionStatus = field(default_factory=SessionStatus)
    resource_version: int = 0
    uid: str = ""


@dataclass
class GrantSpec:
    subject: str
    role: str
    scope: str
    verbs: list[str] = field(default_factory=list)


@dataclass
class GrantStatus:
    pass


@dataclass
class Grant(APIObject):
    kind: ClassVar[str] = "Grant"
    name: str
    spec: GrantSpec
    status: GrantStatus = field(default_factory=GrantStatus)
    resource_version: int = 0
    uid: str = ""


@dataclass
class QuotaSpec:
    scope: str
    tokens: int = 0
    cost: float = 0.0
    concurrency: int = 0


@dataclass
class QuotaStatus:
    used: dict[str, float] = field(default_factory=dict)
    limit: dict[str, float] = field(default_factory=dict)


@dataclass
class Quota(APIObject):
    kind: ClassVar[str] = "Quota"
    name: str
    spec: QuotaSpec
    status: QuotaStatus = field(default_factory=QuotaStatus)
    resource_version: int = 0
    uid: str = ""


@dataclass
class ModelSpec:
    provider: str
    model_id: str
    context: int = 0
    price: float = 0.0
    capabilities: list[str] = field(default_factory=list)


@dataclass
class ModelStatus:
    healthy: bool = False
    version: str = ""


@dataclass
class Model(APIObject):
    kind: ClassVar[str] = "Model"
    name: str
    spec: ModelSpec
    status: ModelStatus = field(default_factory=ModelStatus)
    resource_version: int = 0
    uid: str = ""


@dataclass
class EffectSpec:
    sink_class: str
    payload_hash: str
    idempotency_key: str


@dataclass
class EffectStatus:
    phase: str = "Declared"


@dataclass
class Effect(APIObject):
    """v2 seam: declared, admission-denied in v1."""

    kind: ClassVar[str] = "Effect"
    name: str
    spec: EffectSpec
    status: EffectStatus = field(default_factory=EffectStatus)
    resource_version: int = 0
    uid: str = ""
