"""§17 validation rules + hardcoded-secret scan."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.generator.codegen import GeneratedArtifact
from app.generator.spec import TEMPLATES
from app.validator.naming import evaluate_name

# ADF artifact name rule (§23.4a): PL_/DS_/LS_/TR_ prefixes, letters/digits/_,
# 2–128 chars after the prefix. A project NamingConvention may override.
ARTIFACT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,127}$")

# Real Azure Data Factory *factory* resource name rule — deliberately distinct
# from ARTIFACT_NAME_RE above: a factory name commonly contains hyphens
# (Azure's own resource-naming convention), which ARTIFACT_NAME_RE forbids.
# Only applied once a real factory name is resolved (multi-instance support,
# gap A-2) — the placeholder `<FACTORY_NAME>` token never reaches this check.
FACTORY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{1,61}[A-Za-z0-9]$")

# Heuristic hardcoded-secret patterns (§1.6, §23.5). The generator only ever
# emits Key Vault *references*; a literal here means a bug or bad user input.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("PEM private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("Bearer token literal", re.compile(r"\"(?:Authorization|authorization)\"\s*:\s*\"Bearer\s+[A-Za-z0-9._\-]{16,}\"")),
    ("Password in connection string", re.compile(r"(?i)(?:password|pwd)\s*=\s*(?!@|\{\{|SecretRef:)[^;\"'\s]{3,}")),
    ("AccountKey literal", re.compile(r"(?i)AccountKey=(?!@|\{\{)[A-Za-z0-9+/=]{20,}")),
    ("Shared access signature", re.compile(r"[?&]sig=[A-Za-z0-9%]{20,}")),
    ("GitHub token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("Slack token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("Generic api_key literal", re.compile(r"(?i)\"(?:api_?key|client_?secret|access_?token)\"\s*:\s*\"[A-Za-z0-9._\-]{16,}\"")),
)

# Values the generator legitimately emits that must never trip the scanner.
_ALLOWED_SECRET_SHAPES = re.compile(r"AzureKeyVaultSecret|@\{linkedService\(\)|SecretRef:|@pipeline\(\)|@\{dataset\(\)")


# §29.1/§29.3 — which pyramid layer produced an issue, so the frontend's
# `ValidationPanel` (§29's "unify the 6-layer ValidationResult shape into one
# panel" DoD) can group by layer instead of severity alone. Only the layers
# this validator actually runs today are ever emitted (syntax/semantic/
# security/policy) — layer 2 (vendored ADF schema) and layer 6 (live
# ARM-validate) are still target design (§29.5's "Not done" note) and never
# appear here.
Layer = str  # "syntax" | "schema" | "semantic" | "security" | "policy"


@dataclass
class Issue:
    severity: str  # error | warning | info
    message: str
    source: str | None = None
    field: str | None = None
    layer: Layer = "semantic"
    # §29.3 — stable rule id (Standards catalogue ids where one exists: naming.pattern, security.kv,
    # git.base) and a one-line remediation; `fix_hint` falls back to FIX_HINTS[rule_id].
    rule_id: str | None = None
    fix_hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"severity": self.severity, "message": self.message, "layer": self.layer}
        if self.source:
            out["source"] = self.source
        if self.field:
            out["field"] = self.field
        if self.rule_id:
            out["ruleId"] = self.rule_id
        hint = self.fix_hint or FIX_HINTS.get(self.rule_id or "")
        if hint:
            out["fixHint"] = hint
        return out


FIX_HINTS: dict[str, str] = {
    "spec.template": "Pick an active template (generation strategy) in the Build form.",
    "spec.name.required": "Enter a pipeline name on the Basics step.",
    "spec.name.pattern": "Use lowercase letters, digits and underscores, starting with a letter (3-64 chars).",
    "spec.source.object": "Add at least one source object on the Source step.",
    "spec.target.path": "Set the target path/table on the Target step.",
    "spec.watermark.columns": "Add a watermark column, or switch the load behavior to FullLoad.",
    "spec.watermark.max_columns": "Remove watermark columns until at most 5 remain.",
    "spec.watermark.table": "Set the watermark control table on the Pipeline Settings step.",
    "push.selection": "Select at least one artifact before pushing.",
    "syntax.json": "Regenerate the pipeline; if it persists, report the generator bug.",
    "naming.factory": "Use 3-63 letters, digits or hyphens for the ADF instance name (Administration → Environments).",
    "naming.adf": "Rename using letters, digits and underscores only, starting with a letter.",
    "naming.pattern": "Rename to match the project naming standard (Standards page), then regenerate.",
    "artifact.file_name": "Artifact file names must not contain path separators or '..'.",
    "security.kv": "Replace the literal with a Key Vault secret reference in the connection.",
    "schema.structure": "Regenerate the pipeline; hand-edited artifacts must keep ADF's resource shape.",
    "semantic.key_vault_linked_service": "Confirm the Key Vault linked service exists in the target repo before merging.",
    "security.transport": "Prefer HTTPS / encrypted connections and a non-anonymous auth type where the source allows it.",
    "semantic.expression": "Declare the parameter/variable on the resource, or fix the expression's name (names are case-sensitive).",
    "semantic.reference": "Confirm the referenced artifact already exists in the target repo, or include it in this push.",
    "runtime.retry": "Set a retry count ≥ 1 on the Pipeline Settings step.",
    "docs.description": "Add a description so reviewers know what the resource does.",
    "git.repository": "Configure the repository in Project Settings → Git.",
    "git.feature_branch": "Enter a feature branch name for the push.",
    "git.base": "Push to a feature branch; the PR targets the protected base branch.",
}


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)

    @property
    def state(self) -> str:
        return "failed" if any(i.severity == "error" for i in self.issues) else "passed"

    @property
    def ok(self) -> bool:
        return self.state == "passed"

    def extend(self, issues: list[Issue]) -> None:
        self.issues.extend(issues)

    def to_dict(self) -> dict[str, Any]:
        return {"state": self.state, "issues": [i.to_dict() for i in self.issues]}


# ---------------------------------------------------------------------------
# Secret scan
# ---------------------------------------------------------------------------


def scan_for_secrets(content: str, *, source: str | None = None) -> list[Issue]:
    issues: list[Issue] = []
    for name, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(content):
            window = content[max(0, match.start() - 40) : match.end() + 40]
            if _ALLOWED_SECRET_SHAPES.search(window):
                continue
            issues.append(
                Issue(
                    "error",
                    f"Possible hardcoded secret ({name}) in generated artifact — secrets must be Key Vault references.",
                    source=source,
                    layer="security", rule_id="security.kv"
                )
            )
            break
    return issues


# ---------------------------------------------------------------------------
# §17 rules
# ---------------------------------------------------------------------------


def validate_spec(spec: dict[str, Any]) -> list[Issue]:
    issues: list[Issue] = []
    pipeline = spec.get("pipeline") or {}
    template_id = pipeline.get("type")
    if template_id not in TEMPLATES:  # §17.1
        issues.append(Issue("error", f"Template '{template_id}' does not exist or is not active.", field="templateId", rule_id="spec.template"))

    name = (pipeline.get("name") or "").strip()
    if not name:  # §17.2
        issues.append(Issue("error", "Pipeline name is a required metadata field.", field="name", rule_id="spec.name.required"))
    elif not re.match(r"^[a-z][a-z0-9_]{2,63}$", name):
        issues.append(Issue("error", f"Pipeline name '{name}' violates the naming rule.", field="name", rule_id="spec.name.pattern"))

    source = spec.get("source") or {}
    target = spec.get("target") or {}
    if not (source.get("object") or "").strip():  # §17.2
        issues.append(Issue("error", "Source object is required.", field="source.object", rule_id="spec.source.object"))
    if not (target.get("path") or "").strip():
        issues.append(Issue("error", "Target path is required.", field="target.path", rule_id="spec.target.path"))

    if template_id in TEMPLATES:  # §17.4 — overrides satisfy template schema
        template = TEMPLATES[template_id]
        if source.get("type") and template.supported_source_types and source["type"] not in template.supported_source_types:
            issues.append(
                Issue(
                    "warning",
                    f"Source type '{source['type']}' is outside template '{template_id}' supported set.",
                    field="source.type", rule_id="spec.template.source_type"
                )
            )
        if target.get("type") and template.supported_target_types and target["type"] not in template.supported_target_types:
            issues.append(
                Issue(
                    "warning",
                    f"Target type '{target['type']}' is outside template '{template_id}' supported set.",
                    field="target.type", rule_id="spec.template.target_type"
                )
            )

    watermark = spec.get("watermark") or {}
    load_mode = (spec.get("loadType") or {}).get("mode")
    if watermark.get("enabled") and load_mode != "full":  # §17.3 — valid types
        columns = watermark.get("columns") or []
        if not columns:
            issues.append(Issue("error", "Watermark is enabled but no watermark column is configured.", field="watermark.columns", rule_id="spec.watermark.columns"))
        if len(columns) > 5:
            issues.append(Issue("error", "A composite watermark supports at most 5 columns.", field="watermark.columns", rule_id="spec.watermark.max_columns"))
        if not (watermark.get("watermarkTable") or "").strip():
            issues.append(Issue("error", "A watermark table is required for incremental loads.", field="watermark.watermarkTable", rule_id="spec.watermark.table"))
    return issues


def validate_artifacts(
    artifacts: list[GeneratedArtifact],
    *,
    selected: list[str] | None = None,
    naming_pattern: re.Pattern[str] | None = None,
    naming_convention: dict[str, Any] | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    name_re = naming_pattern or ARTIFACT_NAME_RE

    if selected is not None and len(selected) == 0:  # §17.9
        issues.append(Issue("error", "Select at least one artifact to push.", rule_id="push.selection"))

    for artifact in artifacts:
        if selected is not None and artifact.file_name not in selected:
            continue
        # §17.5 — generated JSON is syntactically valid
        if artifact.file_type == "json":
            try:
                parsed = json.loads(artifact.content)
            except json.JSONDecodeError as exc:
                issues.append(Issue("error", f"Generated JSON is invalid: {exc}.", source=artifact.file_name, layer="syntax", rule_id="syntax.json"))
                parsed = None
            if isinstance(parsed, dict):
                declared = parsed.get("name")
                # The §23.4a prefix rule below governs pipeline/dataset/linkedService/trigger
                # names. "factory" artifacts (globalParameters.json) are a special case: `name`
                # is the placeholder token `<FACTORY_NAME>` when no ADF instance was selected
                # (§20 scope boundary) — never a real ADF resource name, so it can never satisfy
                # an identifier regex and is exempt. Once a real instance is resolved
                # (multi-instance support, gap A-2) `name` is a real factory name, checked
                # against FACTORY_NAME_RE instead of the stricter ARTIFACT_NAME_RE (a factory
                # name may contain hyphens; pipeline/dataset/linkedService/trigger names may not).
                if artifact.artifact_type == "factory":
                    if declared and declared != "<FACTORY_NAME>" and not FACTORY_NAME_RE.match(str(declared)):
                        issues.append(
                            Issue("error", f"Factory name '{declared}' is not a valid ADF factory name.", source=artifact.file_name, rule_id="naming.factory")
                        )
                elif declared and not name_re.match(str(declared)):  # §17.6 — always-on ADF-safe rule
                    issues.append(
                        Issue("error", f"Artifact name '{declared}' violates the configured naming rule.", source=artifact.file_name, rule_id="naming.adf")
                    )
                elif declared:
                    # §25 opt-in project naming standard (prefix/suffix/case/maxLength) — layered on
                    # top of the always-on rule above, only when an admin has turned it on.
                    violation = evaluate_name(str(declared), artifact.artifact_type, naming_convention)
                    if violation:
                        issues.append(
                            Issue(
                                "error" if violation.level == "block" else "warning",
                                f"Naming standard: {violation.message}",
                                source=artifact.file_name,
                                layer="policy", rule_id="naming.pattern"
                            )
                        )
        # §17.6 — file name / path sanity
        if "/" in artifact.file_name or "\\" in artifact.file_name or ".." in artifact.file_name:
            issues.append(Issue("error", f"Artifact file name '{artifact.file_name}' must be a bare file name.", source=artifact.file_name, rule_id="artifact.file_name"))
        # §1.6 / §23.5 — no hardcoded secrets
        issues.extend(scan_for_secrets(artifact.content, source=artifact.file_name))
    return issues


# §29 layer 2 — structural ADF resource-schema checks. Not a vendored copy of the
# full ADF JSON schemas (that remains target design); a hand-kept minimum of the
# shape ADF itself rejects on import: required `properties`/`type`, a dataset's
# linked-service reference, a trigger's pipeline list, and per-pipeline activity
# rules (name + type present, names unique, `dependsOn` resolving to a sibling).
_REQUIRED_TYPE = {"dataset", "linked_service", "trigger", "credential"}
_NESTED_ACTIVITY_KEYS = ("activities", "ifTrueActivities", "ifFalseActivities", "defaultActivities")


def _activity_containers(activities: list[Any]) -> list[list[Any]]:
    """Every activity list in a pipeline — the top level plus each container activity's inner lists."""
    found = [activities]
    for activity in activities:
        tp = activity.get("typeProperties") if isinstance(activity, dict) else None
        if not isinstance(tp, dict):
            continue
        for key in _NESTED_ACTIVITY_KEYS:
            if isinstance(tp.get(key), list):
                found.extend(_activity_containers(tp[key]))
        for case in tp.get("cases") or []:
            if isinstance(case, dict) and isinstance(case.get("activities"), list):
                found.extend(_activity_containers(case["activities"]))
    return found


def _activity_shape_problems(activity: dict[str, Any]) -> list[str]:
    """RUN-12 slice: property shapes ADF rejects that the generator once emitted (RUN-03/05/16)."""
    kind, tp = activity.get("type"), activity.get("typeProperties")
    tp = tp if isinstance(tp, dict) else {}
    problems: list[str] = []
    if kind == "Switch" and ("on" not in tp or "expression" in tp or "default" in tp):
        problems.append("(Switch) needs 'on' and 'defaultActivities', not 'expression'/'default'")
    if kind == "ForEach":
        if "activities" in activity:
            problems.append("(ForEach) must put its inner activities under 'typeProperties.activities'")
        items = tp.get("items")
        value = items.get("value") if isinstance(items, dict) else items
        if isinstance(value, str) and ".output.firstRow" in value:
            problems.append("(ForEach) iterates '.output.firstRow' (one object); use '.output.value'")
    if kind == "Copy" and "truncate" in tp:
        problems.append("(Copy) has no 'truncate' property; truncate the sink with 'preCopyScript'")
    if kind == "Copy":  # RUN-01
        if not (isinstance(activity.get("inputs"), list) and activity["inputs"] and isinstance(activity.get("outputs"), list) and activity["outputs"]):
            problems.append("(Copy) names its datasets in activity-level 'inputs'/'outputs'")
        for side in ("source", "sink"):
            node = tp.get(side) if isinstance(tp.get(side), dict) else {}
            if node.get("type") == "DatasetReference" or {"dataset", "typeProperties"} & set(node):
                problems.append(f"(Copy) '{side}' must be a store type (e.g. SqlServerSource/AzureSqlSink) with no nested dataset or 'typeProperties'")
        if "translation" in tp or "mapping" in tp:
            problems.append("(Copy) maps columns through 'translator' (TabularTranslator), not 'translation'/'mapping'")
        sink = tp.get("sink") if isinstance(tp.get("sink"), dict) else {}
        if "formatSettings" in sink and str(sink.get("type")) not in _FILE_SINK_TYPES:
            problems.append(f"(Copy) '{sink.get('type')}' is not a file sink, so it takes no 'formatSettings'")
    if kind == "Lookup":  # RUN-02
        source = tp.get("source") if isinstance(tp.get("source"), dict) else {}
        if "outputVariableName" in activity or {"query", "sqlWrapperType", "sqlWrapperSourceType"} & set(source):
            problems.append("(Lookup) reads 'source.sqlReaderQuery'; 'query'/'sqlWrapper*'/'outputVariableName' aren't ADF properties")
        query = source.get("sqlReaderQuery")
        if isinstance(query, str) and _WRITE_SQL.search(query) and not _ENDS_WITH_SELECT.search(query):
            problems.append("(Lookup) only writes, but a Lookup needs a result set; end the query with a SELECT")
    return problems


_FILE_SINK_TYPES = {f"{fmt}Sink" for fmt in ("DelimitedText", "Parquet", "Json", "Avro", "Orc", "Binary", "Xml")}
_WRITE_SQL = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE)\b", re.I)
_ENDS_WITH_SELECT = re.compile(r";\s*SELECT\b[^;]*$", re.I)


def _schema_issues(artifact_type: str, doc: dict[str, Any]) -> list[tuple[str, str]]:
    """(severity, problem) pairs."""
    problems: list[tuple[str, str]] = []
    err = lambda msg: problems.append(("error", msg))  # noqa: E731
    props = doc.get("properties")
    if not doc.get("name"):
        err("missing 'name'")
    if not isinstance(props, dict):
        err("missing 'properties' object")
        return problems
    if artifact_type in _REQUIRED_TYPE and not props.get("type"):
        err("missing 'properties.type'")
    if "parameters" in props and not isinstance(props["parameters"], dict):
        err("'properties.parameters' must be a name-keyed object, not a list")
    if artifact_type == "linked_service" and not isinstance(props.get("typeProperties"), dict):
        err("missing 'properties.typeProperties' object")
    if artifact_type == "dataset":
        ref = props.get("linkedServiceName")
        if not isinstance(ref, dict) or not ref.get("referenceName"):
            err("missing 'properties.linkedServiceName.referenceName'")
        # RUN-07: a table dataset's schema/table live in typeProperties; properties.schema is the column array.
        if isinstance(props.get("schema"), str) or "table" in props or "sqlTableType" in props:
            err("table dataset must put 'schema'/'table' under 'properties.typeProperties' (no 'sqlTableType')")
    if artifact_type == "trigger":
        # TumblingWindowTrigger takes a single `pipeline`; every other trigger type a `pipelines` list.
        pipelines = [props["pipeline"]] if isinstance(props.get("pipeline"), dict) else props.get("pipelines")
        if not isinstance(pipelines, list) or not pipelines:
            err("trigger has no 'properties.pipelines' entry")
        else:
            for entry in pipelines:
                ref = entry.get("pipelineReference") if isinstance(entry, dict) else None
                if not isinstance(ref, dict) or ref.get("type") != "PipelineReference":  # RUN-08
                    err("trigger 'pipelineReference' needs \"type\": \"PipelineReference\"")
    if artifact_type == "pipeline":
        activities = props.get("activities")
        if not isinstance(activities, list):
            err("missing 'properties.activities' list")
            return problems
        seen: set[str] = set()
        for container in _activity_containers(activities):
            local: set[str] = set()
            siblings = {a.get("name") for a in container if isinstance(a, dict)}
            for activity in container:
                if not isinstance(activity, dict) or not activity.get("name") or not activity.get("type"):
                    err("an activity is missing 'name' or 'type'")
                    continue
                name = activity["name"]
                if name in local:
                    err(f"duplicate activity name '{name}'")
                elif name in seen:
                    # Same name in two different containers (e.g. two Switch cases) — ADF requires
                    # pipeline-wide unique names. The generator's own Switch-case log activities are
                    # suffixed per branch since 2026-09-26, so this is an error, not a warning.
                    err(f"activity name '{name}' is reused in another container")
                local.add(name)
                seen.add(name)
                for problem in _activity_shape_problems(activity):
                    err(f"activity '{name}' {problem}")
                exec_params = (activity.get("typeProperties") or {}).get("parameters") if activity.get("type") == "ExecutePipeline" else None
                if exec_params is not None and not isinstance(exec_params, dict):
                    err(f"ExecutePipeline '{name}' parameters must be a name-keyed object, not a list")
                for dep in activity.get("dependsOn") or []:
                    target = dep.get("activity") if isinstance(dep, dict) else None
                    if target not in siblings:
                        err(f"activity '{name}' depends on '{target}', which is not in the same container")
    return problems


def validate_resource_schema(artifacts: list[GeneratedArtifact], *, selected: list[str] | None = None) -> list[Issue]:
    issues: list[Issue] = []
    for artifact in artifacts:
        if selected is not None and artifact.file_name not in selected:
            continue
        if artifact.file_type != "json" or artifact.artifact_type not in _REQUIRED_TYPE | {"pipeline"}:
            continue
        try:
            doc = json.loads(artifact.content)
        except json.JSONDecodeError:
            continue  # already reported at the syntax layer
        if not isinstance(doc, dict):
            issues.append(Issue("error", "ADF resource must be a JSON object.", source=artifact.file_name, layer="schema", rule_id="schema.structure"))
            continue
        for severity, problem in _schema_issues(artifact.artifact_type, doc):
            issues.append(Issue(severity, f"ADF resource schema: {problem}.", source=artifact.file_name, layer="schema", rule_id="schema.structure"))
    return issues


def _collect_key_vault_references(node: Any, found: set[str]) -> None:
    """Walk a parsed artifact body collecting ``AzureKeyVaultSecret`` store names."""
    if isinstance(node, dict):
        if node.get("type") == "AzureKeyVaultSecret":
            store = node.get("store")
            if isinstance(store, dict) and store.get("referenceName"):
                found.add(str(store["referenceName"]))
        for value in node.values():
            _collect_key_vault_references(value, found)
    elif isinstance(node, list):
        for item in node:
            _collect_key_vault_references(item, found)


def validate_key_vault_linked_service(
    artifacts: list[GeneratedArtifact], *, selected: list[str] | None = None
) -> list[Issue]:
    """IMPLEMENTATION_PLAN.md A-3 — flag a Key Vault linked service this push
    depends on but does not itself generate.

    Every generated secret reference is an ``AzureKeyVaultSecret`` dynamic
    content block (§1.6/§5/§20, DEVELOPMENT_NOTES.md rule 6) pointing at a Key Vault
    linked service by name — but this app never generates that linked
    service itself (its ``baseUrl`` is customer infrastructure this app has
    no story for, §24.11). A generation-time check can't see the target
    repo's actual contents, so this is a **warning**, not a block: a nudge to
    confirm the referenced linked service already exists before merging.
    """
    considered = [a for a in artifacts if selected is None or a.file_name in selected]
    referenced: set[str] = set()
    for artifact in considered:
        if artifact.file_type != "json":
            continue
        try:
            parsed = json.loads(artifact.content)
        except (json.JSONDecodeError, TypeError):
            continue
        _collect_key_vault_references(parsed, referenced)

    generated_ls_names = {
        a.file_name[: -len(".json")]
        for a in considered
        if a.artifact_type == "linked_service" and a.file_name.endswith(".json")
    }
    missing = sorted(referenced - generated_ls_names)
    return [
        Issue(
            "warning",
            f"This pipeline resolves secrets through the Key Vault linked service '{name}', which this "
            f"push does not generate. Confirm 'linkedService/{name}.json' already exists in the target "
            "repo before merging — every artifact depending on it will otherwise fail to deploy.", rule_id="semantic.key_vault_linked_service"
        )
        for name in missing
    ]


_INSECURE_CONN_FLAGS = (
    (re.compile(r"encrypt\s*=\s*(false|no|optional)", re.I), "disables transport encryption (Encrypt=False)"),
    (re.compile(r"trustservercertificate\s*=\s*(true|yes)", re.I), "skips server certificate validation (TrustServerCertificate=True)"),
)
_URL_KEYS = {"url", "baseUrl", "endpoint", "serviceUri", "restServiceUrl"}
_TLS_OFF_KEYS = {"enableSsl", "useEncryptedEndpoints", "enableServerCertificateValidation", "validateServerCertificate"}


def _insecure_settings(node: Any, found: list[str]) -> None:
    """Walk a linked service body collecting transport/auth settings that weaken security."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _URL_KEYS and isinstance(value, str) and value.lower().startswith("http://"):
                found.append(f"{key} uses plain HTTP")
            elif key in _TLS_OFF_KEYS and value is False:
                found.append(f"{key} is false")
            elif key == "authenticationType" and value == "Anonymous":
                found.append("authenticationType is Anonymous")
            elif key == "connectionString" and isinstance(value, str):
                found.extend(f"connection string {why}" for rx, why in _INSECURE_CONN_FLAGS if rx.search(value))
            else:
                _insecure_settings(value, found)
    elif isinstance(node, list):
        for item in node:
            _insecure_settings(item, found)


def validate_security_posture(
    artifacts: list[GeneratedArtifact], *, selected: list[str] | None = None
) -> list[Issue]:
    """§29.1 layer 4 — flag linked services whose settings weaken transport security or
    authentication (plain HTTP, TLS/certificate validation off, anonymous auth). Warnings, not
    blocks: some on-prem sources genuinely need them, and the reviewer should see it before merge."""
    issues: list[Issue] = []
    for artifact in artifacts:
        if artifact.artifact_type != "linked_service" or artifact.file_type != "json":
            continue
        if selected is not None and artifact.file_name not in selected:
            continue
        try:
            parsed = json.loads(artifact.content)
        except (json.JSONDecodeError, TypeError):
            continue  # syntax layer already reports it
        found: list[str] = []
        _insecure_settings((parsed.get("properties") or {}) if isinstance(parsed, dict) else {}, found)
        for why in dict.fromkeys(found):
            issues.append(
                Issue("warning", f"Linked service security: {why} — confirm this is intended.", source=artifact.file_name, layer="security", rule_id="security.transport")
            )
    return issues


_REFERENCE_KINDS = {
    "PipelineReference": "pipeline",
    "DatasetReference": "dataset",
    "LinkedServiceReference": "linked_service",
    "CredentialReference": "credential",
}


def _collect_references(node: Any, found: list[tuple[str, str]], parent_key: str | None = None) -> None:
    if isinstance(node, dict):
        kind = _REFERENCE_KINDS.get(str(node.get("type")))
        name = node.get("referenceName")
        # `store` = a Key Vault secret's linked service, already covered by validate_key_vault_linked_service.
        if kind and isinstance(name, str) and name and not name.startswith("@") and parent_key != "store":
            found.append((kind, name))
        for key, value in node.items():
            _collect_references(value, found, key)
    elif isinstance(node, list):
        for value in node:
            _collect_references(value, found, parent_key)


def validate_cross_references(
    artifacts: list[GeneratedArtifact], *, selected: list[str] | None = None
) -> list[Issue]:
    """§24.13 / §29.1 layer 3 — every pipeline/dataset/linked-service/credential reference in a
    generated artifact should resolve to an artifact in this generated set. Unresolved ones are
    warnings, not errors: a reused connection, a dispatch child, or a child_only/master_only build
    legitimately points at something already in the repo — the reviewer confirms it exists."""
    declared: dict[str, set[str]] = {kind: set() for kind in _REFERENCE_KINDS.values()}
    parsed_by_file: dict[str, Any] = {}
    for artifact in artifacts:
        if artifact.file_type != "json":
            continue
        try:
            parsed = json.loads(artifact.content)
        except (json.JSONDecodeError, TypeError):
            continue  # syntax layer already reports it
        parsed_by_file[artifact.file_name] = parsed
        if isinstance(parsed, dict) and artifact.artifact_type in declared and parsed.get("name"):
            declared[artifact.artifact_type].add(str(parsed["name"]))
    issues: list[Issue] = []
    for file_name, parsed in parsed_by_file.items():
        if selected is not None and file_name not in selected:
            continue
        refs: list[tuple[str, str]] = []
        _collect_references(parsed, refs)
        for kind, name in dict.fromkeys(refs):
            if name not in declared[kind]:
                issues.append(
                    Issue(
                        "warning",
                        f"References {kind.replace('_', ' ')} '{name}', which is not in this generated set — "
                        "confirm it already exists in the target repo.",
                        source=file_name,
                        layer="semantic", rule_id="semantic.reference"
                    )
                )
    return issues


_EXPRESSION_SCOPES = {
    # artifact_type -> (expression pattern, declaring properties key, label)
    "pipeline": ((re.compile(r"pipeline\(\)\.parameters\.([A-Za-z_][A-Za-z0-9_]*)"), "parameters", "pipeline parameter"),
                 (re.compile(r"variables\('([^']+)'\)"), "variables", "pipeline variable")),
    "dataset": ((re.compile(r"dataset\(\)\.([A-Za-z_][A-Za-z0-9_]*)"), "parameters", "dataset parameter"),),
    "linked_service": ((re.compile(r"linkedService\(\)\.([A-Za-z_][A-Za-z0-9_]*)"), "parameters", "linked service parameter"),),
}


def _declared_names(block: Any) -> set[str]:
    # ADF's shape is a name-keyed object; a list-shaped block is flagged by validate_resource_schema.
    return set(block) if isinstance(block, dict) else set()


def validate_expression_references(
    artifacts: list[GeneratedArtifact], *, selected: list[str] | None = None
) -> list[Issue]:
    """§29.1 layer 3 — every `pipeline().parameters.X` / `variables('X')` / `dataset().X` /
    `linkedService().X` expression must name something the same resource declares; ADF rejects
    the resource on publish otherwise. Errors: an undeclared name can never resolve at runtime."""
    issues: list[Issue] = []
    for artifact in artifacts:
        scopes = _EXPRESSION_SCOPES.get(artifact.artifact_type)
        if not scopes or artifact.file_type != "json":
            continue
        if selected is not None and artifact.file_name not in selected:
            continue
        try:
            parsed = json.loads(artifact.content)
        except (json.JSONDecodeError, TypeError):
            continue  # syntax layer already reports it
        props = parsed.get("properties") if isinstance(parsed, dict) else None
        if not isinstance(props, dict):
            continue  # schema layer already reports it
        for pattern, key, label in scopes:
            declared = _declared_names(props.get(key))
            for name in dict.fromkeys(pattern.findall(artifact.content)):
                if name not in declared:
                    issues.append(
                        Issue("error", f"Expression references {label} '{name}', which this resource does not declare.",
                              source=artifact.file_name, layer="semantic", rule_id="semantic.expression")
                    )
    return issues


_DESCRIBED_TYPES = {"pipeline": "Pipeline", "dataset": "Dataset", "linked_service": "Linked service"}


def _copy_activities_without_retry(node: Any, found: list[str]) -> list[str]:
    if isinstance(node, list):
        for item in node:
            _copy_activities_without_retry(item, found)
    elif isinstance(node, dict):
        if node.get("type") == "Copy" and isinstance(node.get("name"), str):
            try:
                retry = float((node.get("policy") or {}).get("retry") or 0)
            except (TypeError, ValueError):
                retry = 0
            if retry < 1:
                found.append(node["name"])
        for value in node.values():
            _copy_activities_without_retry(value, found)
    return found


def validate_policy_advisories(
    artifacts: list[GeneratedArtifact], *, selected: list[str] | None = None
) -> list[Issue]:
    """§29.1 layer 5 — the Standards page's advisory `runtime.retry`/`docs.description` checks
    (frontend `lib/adf/compliance.ts::advisoryChecks`, kept in parity), run at Validate time.
    Warnings only: advisory rules have no `block` level yet (§25.3)."""
    issues: list[Issue] = []
    for artifact in artifacts:
        label = _DESCRIBED_TYPES.get(artifact.artifact_type)
        if not label or artifact.file_type != "json":
            continue
        if selected is not None and artifact.file_name not in selected:
            continue
        try:
            props = json.loads(artifact.content).get("properties")
        except (json.JSONDecodeError, TypeError, AttributeError):
            continue
        if not isinstance(props, dict):
            continue
        if artifact.artifact_type == "pipeline":
            for name in dict.fromkeys(_copy_activities_without_retry(props.get("activities"), [])):
                issues.append(Issue("warning", f"Copy activity '{name}' has no retry policy (policy.retry < 1).",
                                    source=artifact.file_name, layer="policy", rule_id="runtime.retry"))
        if not (isinstance(props.get("description"), str) and props["description"].strip()):
            issues.append(Issue("warning", f"{label} has no description.",
                                source=artifact.file_name, layer="policy", rule_id="docs.description"))
    return issues


# RUN-12: Spark/ANSI functions and operators with no T-SQL meaning. Vendored from
# backend/app/onboarding/sttm_data_modular.py's _SPARK_ONLY_FUNCTIONS (services can't import each other).
_SPARK_ONLY_FUNCTIONS = (
    "date_format", "to_date", "to_timestamp", "unix_timestamp", "from_unixtime", "initcap", "lpad", "rpad",
    "months_between", "add_months", "last_day", "trunc", "date_trunc", "md5", "sha1", "sha2", "split",
    "explode", "array_contains", "size", "dayofweek", "dayofmonth", "dayofyear", "weekofyear", "quarter",
    "decode", "regexp_replace", "regexp_extract", "collect_list", "collect_set", "substring_index",
    "nvl", "nvl2", "ifnull", "substr", "lcase", "ucase", "date_add", "date_sub", "instr", "current_timestamp",
)
_SPARK_ONLY_CALL = re.compile(r"(?i)\b(" + "|".join(_SPARK_ONLY_FUNCTIONS) + r")\s*\(")
_SPARK_ONLY_SYNTAX = re.compile(r"(?i)<=>|\brlike\b|=\s*(?:true|false)\b")
_SQL_LITERAL = re.compile(r"'(?:[^']|'')*'")
_ADF_INTERPOLATION = re.compile(r"@\{[^{}]*\}")


def _sql_texts(node: Any, found: list[tuple[str, str]], activity: str = "") -> list[tuple[str, str]]:
    """(activity name, SQL text) for every Script text, Lookup/Copy reader query and pre-copy script."""
    if isinstance(node, list):
        for item in node:
            _sql_texts(item, found, activity)
    elif isinstance(node, dict):
        if isinstance(node.get("name"), str) and "type" in node:
            activity = node["name"]
        for key, value in node.items():
            text = value.get("value") if isinstance(value, dict) and value.get("type") == "Expression" else value
            if key in ("text", "sqlReaderQuery", "preCopyScript") and isinstance(text, str):
                # A pure ADF expression (`@concat(...)`) builds SQL at run time; only SQL text is checked.
                if not (text.startswith("@") and not text.startswith("@{")):
                    found.append((activity, text))
            else:
                _sql_texts(value, found, activity)
    return found


def _sql_dialect_problems(sql: str) -> list[str]:
    text = sql
    while (stripped := _ADF_INTERPOLATION.sub("0", text)) != text:
        text = stripped
    if text.count("'") % 2:
        return ["has an unterminated string literal"]
    text = _SQL_LITERAL.sub("''", text)
    problems: list[str] = []
    if text.count("(") != text.count(")"):
        problems.append("has unbalanced parentheses")
    spark = [m.group(1).lower() for m in _SPARK_ONLY_CALL.finditer(text)]
    spark += [m.group(0).split()[0].lower() if m.group(0)[0] != "=" else "= true/false" for m in _SPARK_ONLY_SYNTAX.finditer(text)]
    if spark:
        problems.append(f"uses Spark/ANSI syntax T-SQL doesn't have: {', '.join(dict.fromkeys(spark))}")
    return problems


def validate_sql_dialect(artifacts: list[GeneratedArtifact], *, selected: list[str] | None = None) -> list[Issue]:
    """RUN-12 slice: a lexical T-SQL check over the SQL each pipeline activity runs — catches Spark
    expressions typed into a transformation rule by hand, which the STTM import would have warned about.
    Not a parser: warnings only."""
    issues: list[Issue] = []
    for artifact in artifacts:
        if artifact.artifact_type != "pipeline" or artifact.file_type != "json":
            continue
        if selected is not None and artifact.file_name not in selected:
            continue
        try:
            doc = json.loads(artifact.content)
        except json.JSONDecodeError:
            continue
        for activity, sql in _sql_texts(doc.get("properties") if isinstance(doc, dict) else None, []):
            for problem in _sql_dialect_problems(sql):
                issues.append(Issue("warning", f"Activity '{activity}' SQL {problem}.",
                                    source=artifact.file_name, layer="semantic", rule_id="sql.dialect"))
    return issues


def validate_git(
    *,
    repository_url: str | None,
    feature_branch: str | None,
    base_branch: str | None,
) -> list[Issue]:
    issues: list[Issue] = []
    if not (repository_url or "").strip():  # §17.7
        issues.append(Issue("error", "A Git repository must be configured before pushing.", field="git.repositoryUrl", layer="policy", rule_id="git.repository"))
    feature = (feature_branch or "").strip()
    base = (base_branch or "dev").strip()
    if not feature:
        issues.append(Issue("error", "A feature branch is required for the push.", field="git.featureBranch", layer="policy", rule_id="git.feature_branch"))
    elif feature == base:  # §17.8 / §23.6 — always-on block rule (§25.5)
        issues.append(
            Issue(
                "error", f"The feature branch must not equal the protected base branch '{base}'.",
                field="git.featureBranch", layer="policy", rule_id="git.base"
            )
        )
    return issues


def run_validation(
    spec: dict[str, Any],
    artifacts: list[GeneratedArtifact],
    *,
    repository_url: str | None = None,
    feature_branch: str | None = None,
    base_branch: str | None = None,
    selected: list[str] | None = None,
    naming_pattern: re.Pattern[str] | None = None,
    naming_convention: dict[str, Any] | None = None,
    include_git: bool = False,
) -> ValidationReport:
    report = ValidationReport()
    report.extend(validate_spec(spec))
    report.extend(
        validate_artifacts(artifacts, selected=selected, naming_pattern=naming_pattern, naming_convention=naming_convention)
    )
    report.extend(validate_resource_schema(artifacts, selected=selected))
    report.extend(validate_key_vault_linked_service(artifacts, selected=selected))
    report.extend(validate_security_posture(artifacts, selected=selected))
    report.extend(validate_cross_references(artifacts, selected=selected))
    report.extend(validate_expression_references(artifacts, selected=selected))
    report.extend(validate_policy_advisories(artifacts, selected=selected))
    report.extend(validate_sql_dialect(artifacts, selected=selected))
    if include_git:
        report.extend(validate_git(repository_url=repository_url, feature_branch=feature_branch, base_branch=base_branch))
    return report
