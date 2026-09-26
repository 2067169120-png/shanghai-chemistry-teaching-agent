from __future__ import annotations

import json
import mimetypes
import re
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .adapters import AdapterUnavailable
from .candidate_review import CandidateCropPayload
from .config import CONTRACT_VERSION, AppConfig, Principal
from .public_kb import pagination
from .security import SecurityError, authenticate, safe_join
from .service import ApiError, GatewayService

PERSONAL_AUTO_AUTH_BEARER = "Bearer shchem-local-personal-workbench"
_INTAKE_IMPORT_SOURCE_ROUTE = re.compile(
    r"/api/v1/intake/imports/(INTIMP-[0-9a-f]{32})/source"
)
_INTAKE_IMPORT_PAGE_CONTENT_ROUTE = re.compile(
    r"/api/v1/intake/imports/(INTIMP-[0-9a-f]{32})/pages/([1-9][0-9]?)/content"
)
_STUDENT_VISUAL_FILE_CONTENT_ROUTE = re.compile(
    r"/api/v1/submissions/(SUB-[0-9a-f]{32})/files/(SVF-[0-9a-f]{32})/content"
)
_PRESENTATION_PROJECT_ROUTE = re.compile(
    r"/api/v1/presentations/projects/(PPTPRJ-[0-9a-f]{32})"
)
_PRESENTATION_OUTLINE_ROUTE = re.compile(
    r"/api/v1/presentations/projects/(PPTPRJ-[0-9a-f]{32})/outline"
)
_PRESENTATION_VERSION_ROUTE = re.compile(
    r"/api/v1/presentations/projects/(PPTPRJ-[0-9a-f]{32})/versions"
)
_PRESENTATION_VERSION_ITEM_ROUTE = re.compile(
    r"/api/v1/presentations/projects/(PPTPRJ-[0-9a-f]{32})/versions/"
    r"(PPTVER-[0-9a-f]{64})"
)
_PRESENTATION_RENDER_ROUTE = re.compile(
    r"/api/v1/presentations/projects/(PPTPRJ-[0-9a-f]{32})/versions/"
    r"(PPTVER-[0-9a-f]{64})/renders"
)
_PRESENTATION_JOB_ROUTE = re.compile(
    r"/api/v1/presentations/jobs/(PPTJOB-[0-9a-f]{64})"
)
_PRESENTATION_JOB_ACTION_ROUTE = re.compile(
    r"/api/v1/presentations/jobs/(PPTJOB-[0-9a-f]{64})/(cancel|retry)"
)
_PRESENTATION_ARTIFACT_ROUTE = re.compile(
    r"/api/v1/presentations/jobs/(PPTJOB-[0-9a-f]{64})/artifacts/"
    r"(deck_json|pptx|qa_report|preview_montage)"
)


class GatewayHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], config: AppConfig):
        self.config = config
        self.service = GatewayService(config)
        super().__init__(server_address, GatewayRequestHandler)

    def server_close(self) -> None:
        try:
            self.service.shutdown()
        finally:
            super().server_close()


class GatewayRequestHandler(BaseHTTPRequestHandler):
    server: GatewayHTTPServer
    protocol_version = "HTTP/1.1"
    server_version = "DeepTutorShanghaiChemGateway/1.0"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_OPTIONS(self) -> None:
        request_id = uuid.uuid4().hex
        origin = self.headers.get("Origin")
        if origin and self._origin_allowed(origin):
            self.send_response(HTTPStatus.NO_CONTENT)
            self._common_headers(request_id)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header(
                "Access-Control-Allow-Headers", "Authorization, Content-Type"
            )
            self.send_header(
                "Access-Control-Allow-Methods",
                "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            )
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_error(
            request_id, ApiError("origin_denied", "origin is not allowed", 403)
        )

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        request_id = uuid.uuid4().hex
        principal: Principal | None = None
        status = 500
        student_id: str | None = None
        split = urlsplit(self.path)
        path = unquote(split.path)
        try:
            if not path.startswith("/api/"):
                status = self._serve_static(request_id, path)
                return
            principal = self._authenticate_api_request()
            if method == "PUT" and (
                _INTAKE_IMPORT_SOURCE_ROUTE.fullmatch(path)
                or _STUDENT_VISUAL_FILE_CONTENT_ROUTE.fullmatch(path)
            ):
                body = self._read_intake_upload_body()
            else:
                body = (
                    self._read_json_body()
                    if method in {"POST", "PUT", "PATCH", "DELETE"}
                    else {}
                )
            # Preserve blank query values so read-only routes that forbid every
            # query parameter also reject forms such as ``?x=`` and ``?x``.
            response = self._route(
                method,
                path,
                parse_qs(split.query, keep_blank_values=True),
                principal,
                body,
            )
            if isinstance(response, CandidateCropPayload):
                status = self._send_memory_binary(request_id, response)
            else:
                student_id = (
                    response.pop("_audit_student_id", None)
                    if isinstance(response, dict)
                    else None
                )
                status = (
                    int(response.pop("_http_status", 200))
                    if isinstance(response, dict)
                    else 200
                )
            if isinstance(response, dict) and "_binary_data" in response:
                status = self._send_download_bytes(
                    request_id,
                    bytes(response["_binary_data"]),
                    str(response.get("_binary_content_type") or "application/octet-stream"),
                    download_name=str(response.get("_binary_download_name") or "download.bin"),
                    fallback_name=str(response.get("_binary_fallback_name") or "download.bin"),
                )
            elif isinstance(response, dict) and "_binary_path" in response:
                binary_path = Path(response["_binary_path"])
                status = self._send_binary(
                    request_id,
                    binary_path,
                    str(response.get("_binary_content_type") or "application/zip"),
                    download_name=(
                        str(response["_binary_download_name"])
                        if response.get("_binary_download_name")
                        else None
                    ),
                    fallback_name=(
                        str(response["_binary_fallback_name"])
                        if response.get("_binary_fallback_name")
                        else None
                    ),
                )
            elif not isinstance(response, CandidateCropPayload):
                self._send_json(request_id, status, data=response)
        except SecurityError as exc:
            status = exc.status
            self._send_error(request_id, ApiError(exc.code, str(exc), exc.status))
        except ApiError as exc:
            status = exc.status
            self._send_error(request_id, exc)
        except AdapterUnavailable as exc:
            status = 503
            self._send_error(request_id, ApiError(exc.code, str(exc), 503, exc.details))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            status = 400
            self._send_error(request_id, ApiError("invalid_request", str(exc), 400))
        except (KeyError, OSError):
            status = 500
            self._send_error(
                request_id, ApiError("internal_error", "internal gateway error", 500)
            )
        finally:
            # Governance and isolated candidate-review projections have an
            # explicit zero-write contract. Authentication and Origin checks
            # still run, but their GETs suppress the normal append-only audit.
            zero_write_candidate_read = (
                method == "POST" and path == "/api/v1/questions/search"
            ) or (
                method == "GET"
                and (
                    path
                    in {
                        "/api/v1/readiness",
                        "/api/v1/textbooks",
                        "/api/v1/workbench/product-registry",
                        "/api/v1/kb/workbench/master-atomic",
                        "/api/v1/kb/question-processing-progress",
                    }
                    or path == "/api/v1/kb/workbench/theme-groups"
                    or path.startswith(
                        (
                            "/api/v1/intake/",
                            "/api/v1/kb/sources/candidate_review_only/wave1/",
                            "/api/v1/kb/workbench/master-atomic/",
                            "/api/v1/kb/workbench/master-direct-scans/",
                            "/api/v1/kb/workbench/master-visual-scan-aliases/",
                            "/api/v1/kb/workbench/question-visual-scans/",
                        )
                    )
                )
            )
            if (
                path.startswith("/api/")
                and path != "/api/v1/generation/runs/current/governance"
                and not zero_write_candidate_read
            ):
                self.server.service.audit.write(
                    {
                        "timestamp": self.date_time_string(),
                        "request_id": request_id,
                        "method": method,
                        "path": path,
                        "status": status,
                        "principal_id": principal.principal_id if principal else None,
                        "student_id": student_id,
                        "remote_is_loopback": self.client_address[0]
                        in {"127.0.0.1", "::1"},
                    }
                )

    def _authenticate_api_request(self) -> Principal:
        authorization = self.headers.get("Authorization")
        if (
            self.server.config.personal_auto_auth
            and authorization == PERSONAL_AUTO_AUTH_BEARER
        ):
            if self.client_address[0] not in {"127.0.0.1", "::1"}:
                raise SecurityError(
                    "personal_auto_auth_denied",
                    "personal auto authentication is loopback-only",
                    403,
                )
            origin = self.headers.get("Origin")
            if origin and not self._origin_allowed(origin):
                raise SecurityError(
                    "origin_denied", "origin is not allowed", 403
                )
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise SecurityError(
                    "fetch_site_denied", "cross-site request denied", 403
                )
            teachers = [
                principal
                for principal in self.server.config.principals
                if principal.role == "teacher"
            ]
            if len(teachers) != 1:
                raise SecurityError(
                    "personal_auto_auth_unavailable",
                    "personal auto authentication is unavailable",
                    401,
                )
            return teachers[0]
        return authenticate(authorization, self.server.config.principals)

    def _route(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
        principal: Principal,
        body: dict[str, Any],
    ) -> dict[str, Any] | CandidateCropPayload:
        service = self.server.service
        if method == "POST" and path == "/api/v1/questions/search":
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "question_search_query_parameters_unsupported",
                    "question search accepts filters in its JSON body only",
                    400,
                )
            return service.question_search(principal, body)
        if method == "GET" and path == "/api/v1/textbooks":
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "textbook_catalog_query_unsupported",
                    "教材目录不接受 URL 查询参数。",
                    400,
                )
            return service.textbook_catalog(principal)
        if method == "GET" and path == "/api/v1/readiness":
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "readiness_query_unsupported",
                    "workbench readiness does not accept query parameters",
                    400,
                )
            return service.workbench_readiness(principal)
        if method == "GET" and path == "/api/v1/workbench/product-registry":
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "workbench_registry_query_unsupported",
                    "workbench product registry does not accept query parameters",
                    400,
                )
            return service.workbench_registry(principal)
        if method == "POST" and path == "/api/v1/prep/blueprints/preview":
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "paper_blueprint_query_unsupported",
                    "智能组卷预览不接受 URL 查询参数。",
                    400,
                )
            return service.paper_blueprint_preview(principal, body)
        if method == "POST" and path == "/api/v1/prep/blueprints/approve":
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "paper_blueprint_query_unsupported",
                    "整卷预览确认不接受 URL 查询参数。",
                    400,
                )
            return service.paper_blueprint_approve(principal, body)
        match = re.fullmatch(
            r"/api/v1/prep/blueprints/(PBAPP-[0-9a-f]{32})/export", path
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "paper_blueprint_query_unsupported",
                    "批准后的安全导出不接受 URL 查询参数。",
                    400,
                )
            result = service.paper_blueprint_export(
                principal, match.group(1), body
            )
            result["_http_status"] = 202
            return result
        if method == "POST" and path == "/api/v1/prep/exports":
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "paper_export_query_unsupported",
                    "导出任务不接受 URL 查询参数。",
                    400,
                )
            result = service.paper_export_start(principal, body)
            result["_http_status"] = 202
            return result
        match = re.fullmatch(r"/api/v1/prep/exports/(WBEXP-[0-9a-f]{32})", path)
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "paper_export_query_unsupported",
                    "导出任务状态不接受 URL 查询参数。",
                    400,
                )
            return service.paper_export_get(principal, match.group(1))
        match = re.fullmatch(
            r"/api/v1/prep/exports/(WBEXP-[0-9a-f]{32})/"
            r"artifacts/(student_docx|student_pdf|teacher_docx|teacher_pdf)",
            path,
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "paper_export_query_unsupported",
                    "导出文件下载不接受 URL 查询参数。",
                    400,
                )
            data, content_type, filename = service.paper_export_artifact_bytes(
                principal, match.group(1), match.group(2)
            )
            ascii_names = {
                "student_docx": "shchem-theme-practice-student.docx",
                "student_pdf": "shchem-theme-practice-student.pdf",
                "teacher_docx": "shchem-theme-practice-teacher.docx",
                "teacher_pdf": "shchem-theme-practice-teacher.pdf",
            }
            return {
                "_binary_data": data,
                "_binary_content_type": content_type,
                "_binary_download_name": filename,
                "_binary_fallback_name": ascii_names[match.group(2)],
            }
        if path == "/api/v1/presentations/projects":
            if method == "GET":
                self._require_presentation_read_origin()
                if query:
                    raise ApiError(
                        "presentation_query_unsupported",
                        "PPT 项目不接受 URL 查询参数。",
                        400,
                    )
                return service.presentation_projects(principal)
            if method == "POST":
                self._require_presentation_write_origin()
                if query:
                    raise ApiError(
                        "presentation_query_unsupported",
                        "PPT 项目不接受 URL 查询参数。",
                        400,
                    )
                result = service.presentation_create_from_theme(principal, body)
                result["_http_status"] = 201
                return result
        match = _PRESENTATION_PROJECT_ROUTE.fullmatch(path)
        if method == "GET" and match:
            self._require_presentation_read_origin()
            if query:
                raise ApiError(
                    "presentation_query_unsupported",
                    "PPT 项目详情不接受 URL 查询参数。",
                    400,
                )
            return service.presentation_project_get(principal, match.group(1))
        match = _PRESENTATION_OUTLINE_ROUTE.fullmatch(path)
        if match:
            if method == "GET":
                self._require_presentation_read_origin()
                if query:
                    raise ApiError(
                        "presentation_query_unsupported",
                        "PPT 页纲读取不接受 URL 查询参数。",
                        400,
                    )
                return service.presentation_outline_get(principal, match.group(1))
            if method == "PATCH":
                self._require_presentation_write_origin()
                if query:
                    raise ApiError(
                        "presentation_query_unsupported",
                        "PPT 页纲保存不接受 URL 查询参数。",
                        400,
                    )
                return service.presentation_outline_update(
                    principal, match.group(1), body
                )
        match = _PRESENTATION_VERSION_ROUTE.fullmatch(path)
        if match:
            if method == "GET":
                self._require_presentation_read_origin()
                if query:
                    raise ApiError(
                        "presentation_query_unsupported",
                        "PPT 版本列表不接受 URL 查询参数。",
                        400,
                    )
                return service.presentation_versions(principal, match.group(1))
            if method == "POST":
                self._require_presentation_write_origin()
                if query:
                    raise ApiError(
                        "presentation_query_unsupported",
                        "PPT 版本冻结不接受 URL 查询参数。",
                        400,
                    )
                result = service.presentation_version_create(
                    principal, match.group(1), body
                )
                result["_http_status"] = 201
                return result
        match = _PRESENTATION_VERSION_ITEM_ROUTE.fullmatch(path)
        if method == "GET" and match:
            self._require_presentation_read_origin()
            if query:
                raise ApiError(
                    "presentation_query_unsupported",
                    "PPT 版本详情不接受 URL 查询参数。",
                    400,
                )
            return service.presentation_version_get(
                principal, match.group(1), match.group(2)
            )
        match = _PRESENTATION_RENDER_ROUTE.fullmatch(path)
        if method == "POST" and match:
            self._require_presentation_write_origin()
            if query:
                raise ApiError(
                    "presentation_query_unsupported",
                    "PPT 生成不接受 URL 查询参数。",
                    400,
                )
            result = service.presentation_render_start(
                principal, match.group(1), match.group(2), body
            )
            result["_http_status"] = 202
            return result
        match = _PRESENTATION_JOB_ROUTE.fullmatch(path)
        if method == "GET" and match:
            self._require_presentation_read_origin()
            if query:
                raise ApiError(
                    "presentation_query_unsupported",
                    "PPT 任务状态不接受 URL 查询参数。",
                    400,
                )
            return service.presentation_job_get(principal, match.group(1))
        match = _PRESENTATION_JOB_ACTION_ROUTE.fullmatch(path)
        if method == "POST" and match:
            self._require_presentation_write_origin()
            if query:
                raise ApiError(
                    "presentation_query_unsupported",
                    "PPT 任务操作不接受 URL 查询参数。",
                    400,
                )
            if match.group(2) == "cancel":
                return service.presentation_job_cancel(
                    principal, match.group(1), body
                )
            result = service.presentation_job_retry(
                principal, match.group(1), body
            )
            result["_http_status"] = 202
            return result
        match = _PRESENTATION_ARTIFACT_ROUTE.fullmatch(path)
        if method == "GET" and match:
            self._require_presentation_read_origin()
            if query:
                raise ApiError(
                    "presentation_query_unsupported",
                    "PPT 文件下载不接受 URL 查询参数。",
                    400,
                )
            data, content_type, filename = service.presentation_artifact_bytes(
                principal, match.group(1), match.group(2)
            )
            fallback_names = {
                "deck_json": "lesson-deck.json",
                "pptx": "lesson-presentation.pptx",
                "qa_report": "lesson-presentation-qa.json",
                "preview_montage": "lesson-presentation-preview.png",
            }
            return {
                "_binary_data": data,
                "_binary_content_type": content_type,
                "_binary_download_name": filename,
                "_binary_fallback_name": fallback_names[match.group(2)],
            }
        if method == "GET" and path == "/api/v1/workbench/releases/status":
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "workbench_release_query_unsupported",
                    "release status does not accept query parameters",
                    400,
                )
            return service.workbench_release_status(principal)
        if method == "GET" and path == "/api/v1/workbench/releases/candidates":
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "workbench_release_query_unsupported",
                    "release candidates do not accept query parameters",
                    400,
                )
            return service.workbench_release_candidates(principal)
        if method == "POST" and path == "/api/v1/workbench/releases/candidates":
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "workbench_release_query_unsupported",
                    "candidate freeze does not accept query parameters",
                    400,
                )
            return service.workbench_release_freeze(principal, body)
        match = re.fullmatch(
            r"/api/v1/workbench/releases/(WBREL-[0-9a-f]{64})/"
            r"regressions/(WBRUN-[0-9a-f]{32})/cancel",
            path,
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "workbench_release_query_unsupported",
                    "regression cancellation does not accept query parameters",
                    400,
                )
            return service.workbench_release_regression_cancel(
                principal, match.group(1), match.group(2), body
            )
        match = re.fullmatch(
            r"/api/v1/workbench/releases/(WBREL-[0-9a-f]{64})/"
            r"regressions/(WBRUN-[0-9a-f]{32})",
            path,
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "workbench_release_query_unsupported",
                    "regression status does not accept query parameters",
                    400,
                )
            return service.workbench_release_regression_status(
                principal, match.group(1), match.group(2)
            )
        match = re.fullmatch(
            r"/api/v1/workbench/releases/(WBREL-[0-9a-f]{64})/regressions",
            path,
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "workbench_release_query_unsupported",
                    "fixed regression does not accept query parameters",
                    400,
                )
            return service.workbench_release_regression_start(
                principal, match.group(1), body
            )
        match = re.fullmatch(
            r"/api/v1/workbench/releases/(WBREL-[0-9a-f]{64})/(select|rollback)",
            path,
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "workbench_release_query_unsupported",
                    "release selection does not accept query parameters",
                    400,
                )
            if match.group(2) == "select":
                return service.workbench_release_select(
                    principal, match.group(1), body
                )
            return service.workbench_release_rollback(
                principal, match.group(1), body
            )
        if method == "GET" and path == "/api/v1/settings/model-providers":
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "model_provider_settings_query_unsupported",
                    "model-provider settings do not accept query parameters",
                    400,
                )
            return service.model_provider_settings_list(principal)
        match = re.fullmatch(
            r"/api/v1/settings/model-providers/([^/]+)/credential", path
        )
        if method in {"PUT", "DELETE"} and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "model_provider_settings_query_unsupported",
                    "model-provider credential operations do not accept query parameters",
                    400,
                )
            if method == "PUT":
                return service.model_provider_credential_put(
                    principal, match.group(1), body
                )
            return service.model_provider_credential_delete(
                principal, match.group(1), body
            )
        match = re.fullmatch(
            r"/api/v1/settings/model-providers/([^/]+)/models", path
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "model_provider_settings_query_unsupported",
                    "provider model-list discovery does not accept query parameters",
                    400,
                )
            return service.model_provider_models_probe(
                principal, match.group(1), body
            )
        match = re.fullmatch(
            r"/api/v1/settings/model-providers/([^/]+)/test/([^/]+)/cancel",
            path,
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "model_provider_settings_query_unsupported",
                    "model-provider probe cancellation does not accept query parameters",
                    400,
                )
            return service.model_provider_synthetic_probe_cancel(
                principal, match.group(1), match.group(2), body
            )
        match = re.fullmatch(
            r"/api/v1/settings/model-providers/([^/]+)/test/([^/]+)", path
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "model_provider_settings_query_unsupported",
                    "model-provider probe status does not accept query parameters",
                    400,
                )
            return service.model_provider_synthetic_probe_get(
                principal, match.group(1), match.group(2)
            )
        match = re.fullmatch(
            r"/api/v1/settings/model-providers/([^/]+)/test", path
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "model_provider_settings_query_unsupported",
                    "model-provider probe start does not accept query parameters",
                    400,
                )
            return service.model_provider_synthetic_probe_start(
                principal, match.group(1), body
            )
        match = re.fullmatch(r"/api/v1/settings/model-providers/([^/]+)", path)
        if method == "PUT" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "model_provider_settings_query_unsupported",
                    "model-provider profile updates do not accept query parameters",
                    400,
                )
            return service.model_provider_settings_upsert(
                principal, match.group(1), body
            )
        if method == "GET" and path == "/api/v1/dashboard/status":
            return service.dashboard_status(principal)
        if method == "GET" and path == "/api/v1/kb/taxonomy":
            return service.taxonomy(principal)
        if method == "POST" and path == "/api/v1/kb/search":
            return service.kb_search(principal, body)
        if method == "GET" and path == "/api/v1/kb/retrieval/status":
            return service.retrieval_status(principal)
        if method == "POST" and path == "/api/v1/kb/retrieval/search":
            self._require_safe_browser_read_origin()
            return service.retrieval_search(principal, body)
        if method == "GET" and path == "/api/v1/kb/review-queue":
            limit, offset = pagination(query)
            level = (query.get("level") or [None])[0]
            state = (query.get("state") or [None])[0]
            return service.review_queue(
                principal,
                limit=limit,
                offset=offset,
                level=level,
                state=state,
            )
        if method == "GET" and path == "/api/v1/kb/full-bank-readiness/status":
            self._require_safe_browser_read_origin()
            return service.full_bank_readiness_status(principal)
        if method == "GET" and path == "/api/v1/kb/full-bank-readiness/records":
            self._require_safe_browser_read_origin()
            limit, offset = pagination(query)

            def readiness_one(name: str) -> str | None:
                values = query.get(name, [])
                if len(values) > 1:
                    raise ApiError(
                        "full_bank_readiness_query_ambiguous",
                        f"full-bank readiness query parameter is repeated: {name}",
                        400,
                    )
                return values[0] if values else None

            return service.full_bank_readiness_records(
                principal,
                cohort=readiness_one("cohort"),
                stage=readiness_one("stage"),
                query=readiness_one("q"),
                limit=limit,
                offset=offset,
            )
        if method == "POST" and path == "/api/v1/intake/imports":
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "intake_import_query_unsupported",
                    "intake import creation does not accept query parameters",
                    400,
                )
            return service.intake_import_create(principal, body)
        match = _INTAKE_IMPORT_SOURCE_ROUTE.fullmatch(path)
        if method == "PUT" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "intake_import_query_unsupported",
                    "intake source upload does not accept query parameters",
                    400,
                )
            raw = body.get("_intake_upload_bytes")
            content_type = body.get("_intake_upload_content_type")
            if not isinstance(raw, bytes) or not isinstance(content_type, str):
                raise ApiError("intake_upload_invalid", "intake upload body is invalid", 400)
            return service.intake_import_upload(
                principal,
                match.group(1),
                raw,
                content_type=content_type,
            )
        match = re.fullmatch(
            r"/api/v1/intake/imports/(INTIMP-[0-9a-f]{32})/analyze", path
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "intake_import_query_unsupported",
                    "intake analysis does not accept query parameters",
                    400,
                )
            return service.intake_import_analyze(
                principal, match.group(1), body
            )
        match = re.fullmatch(
            r"/api/v1/intake/imports/(INTIMP-[0-9a-f]{32})/review", path
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "intake_import_query_unsupported",
                    "intake review does not accept query parameters",
                    400,
                )
            return service.intake_import_review(
                principal, match.group(1), body
            )
        match = _INTAKE_IMPORT_PAGE_CONTENT_ROUTE.fullmatch(path)
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "intake_import_query_unsupported",
                    "intake page content does not accept query parameters",
                    400,
                )
            return service.intake_import_page_content(
                principal, match.group(1), int(match.group(2))
            )
        if method == "GET" and path == "/api/v1/intake/personal-library":
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "intake_import_query_unsupported",
                    "personal import library does not accept query parameters",
                    400,
                )
            return service.intake_import_personal_library(principal)
        match = re.fullmatch(
            r"/api/v1/intake/imports/(INTIMP-[0-9a-f]{32})/cancel", path
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "intake_import_query_unsupported",
                    "intake cancellation does not accept query parameters",
                    400,
                )
            return service.intake_import_cancel(principal, match.group(1), body)
        match = re.fullmatch(
            r"/api/v1/intake/imports/(INTIMP-[0-9a-f]{32})", path
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "intake_import_query_unsupported",
                    "intake import status does not accept query parameters",
                    400,
                )
            return service.intake_import_get(principal, match.group(1))
        if method == "GET" and path == "/api/v1/intake/status":
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "material_intake_query_unsupported",
                    "material intake status does not accept query parameters",
                    400,
                )
            return service.material_intake_status(principal)
        if method == "GET" and path == "/api/v1/intake/batches":
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "material_intake_query_unsupported",
                    "material intake batch list does not accept query parameters",
                    400,
                )
            return service.material_intake_batches(principal)
        match = re.fullmatch(r"/api/v1/intake/batches/(INTAKE-BATCH-[0-9a-f]{40})", path)
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "material_intake_query_unsupported",
                    "material intake batch detail does not accept query parameters",
                    400,
                )
            return service.material_intake_batch_detail(principal, match.group(1))
        if method == "GET" and path == "/api/v1/intake/records":
            self._require_safe_browser_read_origin()
            if set(query) - {"kind", "stage", "status", "q", "limit", "offset"}:
                raise ApiError(
                    "material_intake_query_unsupported",
                    "material intake record query contains an unsupported parameter",
                    400,
                )
            if any(
                len(query.get(name, [])) > 1
                for name in ("kind", "stage", "status", "q", "limit", "offset")
            ):
                raise ApiError(
                    "material_intake_query_ambiguous",
                    "material intake query parameters must be unique",
                    400,
                )
            limit, offset = pagination(query)

            def intake_one(name: str) -> str | None:
                values = query.get(name, [])
                return values[0] if values else None

            return service.material_intake_records(
                principal,
                kind=intake_one("kind"),
                stage=intake_one("stage"),
                status=intake_one("status"),
                query=intake_one("q"),
                limit=limit,
                offset=offset,
            )
        if method == "GET" and path == "/api/v1/kb/question-processing-progress":
            self._require_safe_browser_read_origin()
            allowed = {"scope", "paper_id", "theme_id", "gap", "limit", "offset"}
            if set(query) - allowed:
                raise ApiError(
                    "question_processing_progress_query_unsupported",
                    "question-processing progress contains an unsupported parameter",
                    400,
                )
            if any(len(query.get(name, [])) > 1 for name in allowed):
                raise ApiError(
                    "question_processing_progress_query_ambiguous",
                    "question-processing progress parameters must be unique",
                    400,
                )
            if len(query.get("scope", [])) != 1:
                raise ApiError(
                    "question_processing_progress_scope_required",
                    "question-processing progress requires exactly one scope",
                    400,
                )
            try:
                limit = int((query.get("limit") or ["50"])[0])
                offset = int((query.get("offset") or ["0"])[0])
            except (TypeError, ValueError) as exc:
                raise ApiError(
                    "question_processing_progress_pagination_invalid",
                    "question-processing progress pagination must use integers",
                    400,
                ) from exc
            if not 1 <= limit <= 100 or offset < 0:
                raise ApiError(
                    "question_processing_progress_pagination_invalid",
                    "question-processing progress pagination is outside the supported range",
                    400,
                )

            def progress_one(name: str) -> str | None:
                values = query.get(name, [])
                return values[0] if values else None

            return service.question_processing_progress(
                principal,
                scope=query["scope"][0],
                paper_id=progress_one("paper_id"),
                theme_id=progress_one("theme_id"),
                gap=progress_one("gap"),
                limit=limit,
                offset=offset,
            )
        if method == "GET" and path == "/api/v1/kb/workbench/theme-groups":
            self._require_safe_browser_read_origin()
            if set(query) != {"scope"} or len(query.get("scope", [])) != 1:
                raise ApiError(
                    "theme_workbench_query_unsupported",
                    "theme groups require exactly one scope query parameter",
                    400,
                )
            scope = query["scope"][0]
            if scope not in {"wave1", "master", "supplemental"}:
                raise ApiError(
                    "theme_workbench_scope_invalid",
                    "scope must be exactly wave1, master, or supplemental",
                    400,
                )
            return service.theme_workbench_groups(principal, scope=scope)
        if (
            method == "GET"
            and path == "/api/v1/kb/workbench/supplemental-scans/status"
        ):
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "supplemental_scan_query_unsupported",
                    "supplemental scan status does not accept query parameters",
                    400,
                )
            return service.supplemental_visual_scan_status(principal)
        if method == "GET" and path == "/api/v1/kb/workbench/supplemental-scans":
            self._require_safe_browser_read_origin()
            if set(query) - {"limit", "offset"} or any(
                len(query.get(name, [])) > 1 for name in ("limit", "offset")
            ):
                raise ApiError(
                    "supplemental_scan_query_unsupported",
                    "supplemental scan list accepts one limit and one offset only",
                    400,
                )
            try:
                limit = int((query.get("limit") or ["200"])[0])
                offset = int((query.get("offset") or ["0"])[0])
            except (TypeError, ValueError) as exc:
                raise ApiError(
                    "supplemental_scan_pagination_invalid",
                    "supplemental scan pagination must use integers",
                    400,
                ) from exc
            if not 1 <= limit <= 200 or not 0 <= offset <= 100000:
                raise ApiError(
                    "supplemental_scan_pagination_invalid",
                    "supplemental scan pagination is outside the supported range",
                    400,
                )
            return service.supplemental_visual_scan_list(
                principal, limit=limit, offset=offset
            )
        match = re.fullmatch(
            r"/api/v1/kb/workbench/supplemental-scans/([^/]+)/question-crops/([^/]+)",
            path,
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "supplemental_scan_query_unsupported",
                    "supplemental scan crops do not accept query parameters",
                    400,
                )
            return service.supplemental_visual_scan_question_crop(
                principal, *match.groups()
            )
        match = re.fullmatch(
            r"/api/v1/kb/workbench/supplemental-scans/([^/]+)", path
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "supplemental_scan_query_unsupported",
                    "supplemental scan detail does not accept query parameters",
                    400,
                )
            return service.supplemental_visual_scan_detail(
                principal, match.group(1)
            )
        if method == "GET" and path == "/api/v1/kb/workbench/master-atomic/status":
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "master_wave1_query_unsupported",
                    "master atomic status does not accept query parameters",
                    400,
                )
            return service.master_atomic_workbench_status(principal)
        if (
            method == "GET"
            and path == "/api/v1/kb/workbench/question-visual-scans/status"
        ):
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "question_visual_scan_query_unsupported",
                    "question visual scan status does not accept query parameters",
                    400,
                )
            return service.question_visual_scan_status(principal)
        if (
            method == "GET"
            and path == "/api/v1/kb/workbench/question-visual-scans/catalog"
        ):
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "question_visual_scan_query_unsupported",
                    "question visual scan catalog does not accept query parameters",
                    400,
                )
            return service.question_visual_scan_catalog(principal)
        match = re.fullmatch(
            r"/api/v1/kb/workbench/question-visual-scans/([^/]+)", path
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "question_visual_scan_query_unsupported",
                    "question visual scan detail does not accept query parameters",
                    400,
                )
            return service.question_visual_scan_detail(principal, match.group(1))
        if (
            method == "GET"
            and path == "/api/v1/kb/workbench/master-direct-scans/status"
        ):
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "master_direct_scan_query_unsupported",
                    "master direct visual scan status does not accept query parameters",
                    400,
                )
            return service.master_direct_visual_scan_status(principal)
        if (
            method == "GET"
            and path == "/api/v1/kb/workbench/master-direct-scans/catalog"
        ):
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "master_direct_scan_query_unsupported",
                    "master direct visual scan catalog does not accept query parameters",
                    400,
                )
            return service.master_direct_visual_scan_catalog(principal)
        match = re.fullmatch(
            r"/api/v1/kb/workbench/master-direct-scans/([^/]+)/question-crops/([^/]+)",
            path,
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "master_direct_scan_query_unsupported",
                    "master direct visual scan crops do not accept query parameters",
                    400,
                )
            return service.master_direct_visual_scan_question_crop(
                principal, *match.groups()
            )
        match = re.fullmatch(
            r"/api/v1/kb/workbench/master-direct-scans/([^/]+)", path
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "master_direct_scan_query_unsupported",
                    "master direct visual scan detail does not accept query parameters",
                    400,
                )
            return service.master_direct_visual_scan_detail(principal, match.group(1))
        if (
            method == "GET"
            and path == "/api/v1/kb/workbench/master-visual-scan-aliases/status"
        ):
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "master_visual_scan_alias_query_unsupported",
                    "master visual-scan alias status does not accept query parameters",
                    400,
                )
            return service.master_visual_scan_alias_status(principal)
        if (
            method == "GET"
            and path == "/api/v1/kb/workbench/master-visual-scan-aliases/catalog"
        ):
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "master_visual_scan_alias_query_unsupported",
                    "master visual-scan alias catalog does not accept query parameters",
                    400,
                )
            return service.master_visual_scan_alias_catalog(principal)
        match = re.fullmatch(
            r"/api/v1/kb/workbench/master-visual-scan-aliases/([^/]+)/question-crops/([^/]+)",
            path,
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "master_visual_scan_alias_query_unsupported",
                    "master visual-scan alias crops do not accept query parameters",
                    400,
                )
            return service.master_visual_scan_alias_question_crop(
                principal, *match.groups()
            )
        match = re.fullmatch(
            r"/api/v1/kb/workbench/master-visual-scan-aliases/([^/]+)", path
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "master_visual_scan_alias_query_unsupported",
                    "master visual-scan alias detail does not accept query parameters",
                    400,
                )
            return service.master_visual_scan_alias_detail(principal, match.group(1))
        if method == "GET" and path == "/api/v1/kb/workbench/master-atomic":
            self._require_safe_browser_read_origin()
            if set(query) - {"limit", "offset"} or any(
                len(query.get(name, [])) > 1 for name in ("limit", "offset")
            ):
                raise ApiError(
                    "master_wave1_query_unsupported",
                    "master atomic list accepts one limit and one offset only",
                    400,
                )
            normalized_query = dict(query)
            normalized_query.setdefault("limit", ["200"])
            limit, offset = pagination(normalized_query, max_limit=200)
            return service.master_atomic_workbench_list(
                principal, limit=limit, offset=offset
            )
        match = re.fullmatch(
            r"/api/v1/kb/workbench/master-atomic/([^/]+)", path
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "master_wave1_query_unsupported",
                    "master atomic detail does not accept query parameters",
                    400,
                )
            return service.master_atomic_workbench_detail(
                principal, match.group(1)
            )
        if (
            method == "GET"
            and path
            == "/api/v1/kb/sources/candidate_review_only/wave1/status"
        ):
            self._require_safe_browser_read_origin()
            return service.candidate_review_status(principal)
        if (
            method == "GET"
            and path
            == "/api/v1/kb/sources/candidate_review_only/wave1/nodes"
        ):
            self._require_safe_browser_read_origin()
            limit, offset = pagination(query, max_limit=200)

            def one(name: str) -> str | None:
                values = query.get(name, [])
                if len(values) > 1:
                    raise ApiError(
                        "candidate_review_query_ambiguous",
                        f"candidate review query parameter is repeated: {name}",
                        400,
                    )
                return values[0] if values else None

            return service.candidate_review_list(
                principal,
                node_type=one("node_type"),
                query=one("q"),
                paper_id=one("paper_id"),
                theme_id=one("theme_id"),
                printed_question_id=one("printed_question_id"),
                limit=limit,
                offset=offset,
            )
        crop_match = re.fullmatch(
            r"/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
            r"atomic_part/([^/]+)/question-crops/([^/]+)",
            path,
        )
        if method == "GET" and crop_match:
            self._require_safe_browser_read_origin()
            return service.candidate_review_question_crop(
                principal, *crop_match.groups()
            )
        match = re.fullmatch(
            r"/api/v1/kb/sources/candidate_review_only/wave1/nodes/"
            r"(paper|theme_big_question|printed_question|atomic_part)/([^/]+)",
            path,
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            return service.candidate_review_node(principal, *match.groups())
        if method == "GET" and path == "/api/v1/review/tasks":
            self._require_safe_browser_read_origin()
            allowed = {"task_kind", "paper_id", "state", "limit", "offset"}
            unknown = sorted(set(query) - allowed)
            if unknown or any(len(values) != 1 for values in query.values()):
                raise ApiError(
                    "theme_review_query_invalid",
                    "整主题复核任务查询参数无效或重复",
                    400,
                )
            limit, offset = pagination(query, max_limit=200)

            def review_filter(name: str) -> str | None:
                values = query.get(name)
                return values[0] if values else None

            return service.theme_review_task_list(
                principal,
                task_kind=review_filter("task_kind"),
                paper_id=review_filter("paper_id"),
                state=review_filter("state"),
                limit=limit,
                offset=offset,
            )
        match = re.fullmatch(
            r"/api/v1/review/tasks/([^/]+)/change-sets/([^/]+)/preview", path
        )
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "theme_review_query_invalid", "候选层预览不接受查询参数", 400
                )
            return service.theme_review_change_set_preview(
                principal, *match.groups()
            )
        match = re.fullmatch(
            r"/api/v1/review/tasks/([^/]+)/(claim|release|change-sets|decisions)",
            path,
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "theme_review_query_invalid", "整主题复核写入不接受查询参数", 400
                )
            task_id, action = match.groups()
            if action == "claim":
                return service.theme_review_task_claim(principal, task_id, body)
            if action == "release":
                return service.theme_review_task_release(principal, task_id, body)
            if action == "change-sets":
                result = service.theme_review_change_set_create(
                    principal, task_id, body
                )
            else:
                result = service.theme_review_decision_create(
                    principal, task_id, body
                )
            result["_http_status"] = 201
            return result
        match = re.fullmatch(r"/api/v1/review/tasks/([^/]+)", path)
        if method == "GET" and match:
            self._require_safe_browser_read_origin()
            if query:
                raise ApiError(
                    "theme_review_query_invalid", "整主题复核任务详情不接受查询参数", 400
                )
            return service.theme_review_task_get(principal, match.group(1))
        if (
            method in {"GET", "POST"}
            and path == "/api/v1/tagging/workbench/tag-patches/candidates"
        ):
            if method == "GET":
                limit, offset = pagination(query)
                node_id = (query.get("node_id") or [None])[0]
                return service.tag_patch_list(
                    principal,
                    node_id=node_id,
                    limit=limit,
                    offset=offset,
                )
            self._require_safe_browser_write_origin()
            result = service.tag_patch_create(principal, body)
            result["_http_status"] = 201
            return result
        match = re.fullmatch(
            r"/api/v1/tagging/workbench/tag-patches/candidates/([^/]+)", path
        )
        if method == "GET" and match:
            return service.tag_patch_get(principal, match.group(1))
        match = re.fullmatch(
            r"/api/v1/kb/hierarchy/(paper|theme_big_question|printed_question|atomic_part)/([^/]+)/(children|evidence)",
            path,
        )
        if method == "GET" and match:
            node_type, node_id, view = match.groups()
            if view == "children":
                limit, offset = pagination(query)
                return service.hierarchy_children(
                    principal, node_type, node_id, limit=limit, offset=offset
                )
            return service.hierarchy_evidence(principal, node_type, node_id)
        match = re.fullmatch(
            r"/api/v1/kb/hierarchy/(paper|theme_big_question|printed_question|atomic_part)/([^/]+)",
            path,
        )
        if method == "GET" and match:
            return service.hierarchy_node(principal, *match.groups())
        if method == "GET" and path == "/api/v1/generation/runs":
            return service.generation_run_list(principal)
        if method == "GET" and path == "/api/v1/generation/runs/current":
            return service.generation_run_current(principal)
        if (
            method == "GET"
            and path == "/api/v1/generation/runs/current/governance"
        ):
            self._require_safe_browser_read_origin()
            return service.generation_run_governance(principal)
        if method == "GET" and path == "/api/v1/generation/runs/current/artifacts":
            return service.generation_run_artifacts(principal, "r18")
        if (
            method in {"GET", "POST"}
            and path
            == "/api/v1/generation/workbench/task-cards/candidates"
        ):
            if method == "GET":
                return service.workbench_task_list(principal)
            result = service.workbench_task_create(principal, body)
            result["_http_status"] = 201
            return result
        match = re.fullmatch(
            r"/api/v1/generation/workbench/task-cards/candidates/([^/]+)/freeze",
            path,
        )
        if method == "POST" and match:
            result = service.workbench_task_freeze(
                principal, match.group(1), body
            )
            result["_http_status"] = 201
            return result
        match = re.fullmatch(
            r"/api/v1/generation/workbench/task-cards/candidates/([^/]+)/plan-runs",
            path,
        )
        if method == "POST" and match:
            result = service.workbench_plan_run_create(
                principal, match.group(1), body
            )
            result["_http_status"] = 201
            return result
        match = re.fullmatch(
            r"/api/v1/generation/workbench/task-cards/candidates/([^/]+)",
            path,
        )
        if method == "GET" and match:
            return service.workbench_task_get(principal, match.group(1))
        match = re.fullmatch(
            r"/api/v1/generation/workbench/plan-runs/([^/]+)/(events|artifacts)",
            path,
        )
        if method == "GET" and match:
            run_id, view = match.groups()
            if view == "events":
                return service.workbench_plan_run_events(principal, run_id)
            return service.workbench_plan_run_artifacts(principal, run_id)
        match = re.fullmatch(
            r"/api/v1/generation/workbench/plan-runs/([^/]+)", path
        )
        if method == "GET" and match:
            return service.workbench_plan_run_get(principal, match.group(1))
        match = re.fullmatch(r"/api/v1/generation/runs/([^/]+)/artifacts", path)
        if method == "GET" and match:
            return service.generation_run_artifacts(principal, match.group(1))
        match = re.fullmatch(r"/api/v1/generation/runs/([^/]+)", path)
        if method == "GET" and match:
            return service.generation_run_get(principal, match.group(1))
        if method == "GET" and path == "/api/v1/status":
            return service.status()
        if method == "GET" and path == "/api/v1/validate":
            return service.validate()
        if method == "POST" and path == "/api/v1/students":
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "student_profile_query_unsupported",
                    "student profile creation does not accept query parameters",
                    400,
                )
            result = service.create_student_profile(principal, body)
            result["_audit_student_id"] = result.get("student_id")
            result["_http_status"] = 201
            return result
        if method == "GET" and path == "/api/v1/students":
            return service.list_students(principal)
        if method == "POST" and path == "/api/v1/submissions":
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "submission_query_unsupported",
                    "submission creation does not accept query parameters",
                    400,
                )
            result = service.submission_create(principal, body)
            result["_audit_student_id"] = result.get("student_id")
            result["_http_status"] = 201
            return result
        match = re.fullmatch(
            r"/api/v1/submissions/(SUB-[0-9a-f]{32})/files", path
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "submission_query_unsupported",
                    "submission file registration does not accept query parameters",
                    400,
                )
            result = service.submission_file_register(
                principal, match.group(1), body
            )
            result["_audit_student_id"] = result.get("student_id")
            result["_http_status"] = 201
            return result
        match = _STUDENT_VISUAL_FILE_CONTENT_ROUTE.fullmatch(path)
        if method == "PUT" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "submission_query_unsupported",
                    "submission file upload does not accept query parameters",
                    400,
                )
            raw = body.get("_intake_upload_bytes")
            content_type = body.get("_intake_upload_content_type")
            if not isinstance(raw, bytes) or not isinstance(content_type, str):
                raise ApiError(
                    "submission_upload_invalid", "submission upload body is invalid"
                )
            result = service.submission_file_upload(
                principal,
                match.group(1),
                match.group(2),
                raw,
                content_type=content_type,
            )
            result["_audit_student_id"] = result.get("student_id")
            return result
        match = re.fullmatch(
            r"/api/v1/submissions/(SUB-[0-9a-f]{32})/matching", path
        )
        if method in {"GET", "PATCH"} and match:
            if query:
                raise ApiError(
                    "submission_query_unsupported",
                    "submission matching does not accept query parameters",
                    400,
                )
            if method == "PATCH":
                self._require_safe_browser_write_origin()
                result = service.submission_matching_update(
                    principal, match.group(1), body
                )
            else:
                result = service.submission_matching_get(
                    principal, match.group(1)
                )
            result["_audit_student_id"] = result.get("student_id")
            return result
        match = re.fullmatch(
            r"/api/v1/submissions/(SUB-[0-9a-f]{32})/privacy-decisions",
            path,
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "submission_query_unsupported",
                    "privacy decisions do not accept query parameters",
                    400,
                )
            result = service.submission_privacy_decision(
                principal, match.group(1), body
            )
            result["_audit_student_id"] = result.get("student_id")
            result["_http_status"] = 201
            return result
        match = re.fullmatch(
            r"/api/v1/submissions/(SUB-[0-9a-f]{32})/(analyze|cancel)", path
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "submission_query_unsupported",
                    "submission analysis actions do not accept query parameters",
                    400,
                )
            if match.group(2) == "analyze":
                result = service.submission_analyze(
                    principal, match.group(1), body
                )
                result["_http_status"] = 202
            else:
                result = service.submission_cancel(
                    principal, match.group(1), body
                )
            result["_audit_student_id"] = result.get("student_id")
            return result
        match = re.fullmatch(
            r"/api/v1/submissions/(SUB-[0-9a-f]{32})/(analysis|review)", path
        )
        if method == "GET" and match:
            if query:
                raise ApiError(
                    "submission_query_unsupported",
                    "submission review does not accept query parameters",
                    400,
                )
            if match.group(2) == "analysis":
                result = service.submission_analysis_get(
                    principal, match.group(1)
                )
            else:
                result = service.submission_review_get(
                    principal, match.group(1)
                )
            result["_audit_student_id"] = result.get("student_id")
            return result
        match = re.fullmatch(
            r"/api/v1/submissions/(SUB-[0-9a-f]{32})/"
            r"(diagnostic-review|recommendation-preview)",
            path,
        )
        if method == "GET" and match:
            if query:
                raise ApiError(
                    "submission_query_unsupported",
                    "student diagnosis preview does not accept query parameters",
                    400,
                )
            if match.group(2) == "diagnostic-review":
                result = service.submission_diagnostic_review_get(
                    principal, match.group(1)
                )
            else:
                result = service.submission_recommendation_preview(
                    principal, match.group(1)
                )
            result["_audit_student_id"] = result.get("student_id")
            return result
        match = re.fullmatch(
            r"/api/v1/submissions/(SUB-[0-9a-f]{32})/scoring-decisions",
            path,
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "submission_query_unsupported",
                    "scoring decisions do not accept query parameters",
                    400,
                )
            result = service.submission_scoring_decision(
                principal, match.group(1), body
            )
            result["_audit_student_id"] = result.get("student_id")
            result["_http_status"] = 201
            return result
        match = re.fullmatch(
            r"/api/v1/submissions/(SUB-[0-9a-f]{32})/diagnostic-decisions",
            path,
        )
        if method == "POST" and match:
            self._require_safe_browser_write_origin()
            if query:
                raise ApiError(
                    "submission_query_unsupported",
                    "diagnostic decisions do not accept query parameters",
                    400,
                )
            result = service.submission_diagnostic_decision(
                principal, match.group(1), body
            )
            result["_audit_student_id"] = result.get("student_id")
            result["_http_status"] = 201
            return result
        match = re.fullmatch(
            r"/api/v1/submissions/(SUB-[0-9a-f]{32})(?:/status)?", path
        )
        if method == "GET" and match:
            if query:
                raise ApiError(
                    "submission_query_unsupported",
                    "submission status does not accept query parameters",
                    400,
                )
            result = service.submission_get(principal, match.group(1))
            result["_audit_student_id"] = result.get("student_id")
            return result
        match = re.fullmatch(r"/api/v1/students/([^/]+)/learning-view", path)
        if method == "GET" and match:
            student_id = match.group(1)
            result = service.learning_view(principal, student_id)
            result["_audit_student_id"] = student_id
            return result
        if method == "POST" and path == "/api/v1/evidence/search":
            return service.evidence_search(body)
        match = re.fullmatch(r"/api/v1/evidence/([^/]+)", path)
        if method == "GET" and match:
            return service.evidence_get(match.group(1))
        if method == "POST" and path == "/api/v1/preflight":
            student_id = body.get("student_id")
            result = service.preflight(
                principal, body, str(student_id) if student_id else None
            )
            if student_id:
                result["_audit_student_id"] = str(student_id)
            return result
        if method == "POST" and path == "/api/v1/jobs/candidates":
            student_id = str(body.get("student_id", ""))
            kind = str(body.get("kind", ""))
            payload = dict(body)
            payload.pop("student_id", None)
            payload.pop("kind", None)
            result = service.create_candidate_job(principal, student_id, kind, payload)
            result["_audit_student_id"] = student_id
            result["_http_status"] = 202
            return result
        match = re.fullmatch(r"/api/v1/students/([^/]+)/uploads", path)
        if method == "POST" and match:
            student_id = match.group(1)
            result = service.save_upload(principal, student_id, body)
            result["_audit_student_id"] = student_id
            result["_http_status"] = 201
            return result
        match = re.fullmatch(r"/api/v1/students/([^/]+)/diagnosis", path)
        if method == "POST" and match:
            student_id = match.group(1)
            result = service.create_candidate_job(
                principal, student_id, "student_diagnosis", body
            )
            result["_audit_student_id"] = student_id
            result["_http_status"] = 202
            return result
        match = re.fullmatch(r"/api/v1/students/([^/]+)/handouts/candidates", path)
        if method == "POST" and match:
            student_id = match.group(1)
            result = service.create_candidate_job(
                principal, student_id, "handout_candidate", body
            )
            result["_audit_student_id"] = student_id
            result["_http_status"] = 202
            return result
        match = re.fullmatch(r"/api/v1/students/([^/]+)/jobs/([^/]+)", path)
        if method == "GET" and match:
            student_id, job_id = match.groups()
            result = service.get_job(principal, student_id, job_id)
            result["_audit_student_id"] = student_id
            return result
        match = re.fullmatch(
            r"/api/v1/students/([^/]+)/jobs/([^/]+)/artifact\.zip", path
        )
        if method == "GET" and match:
            student_id, job_id = match.groups()
            artifact = service.artifact_path(principal, student_id, job_id)
            return {
                "_binary_path": str(artifact),
                "_audit_student_id": student_id,
            }
        match = re.fullmatch(r"/api/v1/jobs/([^/]+)", path)
        if method == "GET" and match:
            student_id = (query.get("student_id") or [""])[0]
            result = service.get_job(principal, student_id, match.group(1))
            result["_audit_student_id"] = student_id
            return result
        match = re.fullmatch(r"/api/v1/jobs/([^/]+)/artifact\.zip", path)
        if method == "GET" and match:
            student_id = (query.get("student_id") or [""])[0]
            artifact = service.artifact_path(principal, student_id, match.group(1))
            return {
                "_binary_path": str(artifact),
                "_audit_student_id": student_id,
            }
        match = re.fullmatch(r"/api/v1/figures/([^/]+)", path)
        if method == "GET" and match:
            return service.figure_get(match.group(1))
        if method == "POST" and path == "/api/v1/publication/preflight":
            return service.publication_preflight(body)
        if path.startswith("/api/v1/presentations"):
            raise ApiError(
                "presentation_route_not_found",
                "找不到这个课程 PPT 接口。",
                404,
            )
        raise ApiError("route_not_found", "API route not found", 404)

    def _require_presentation_write_origin(self) -> None:
        try:
            self._require_safe_browser_write_origin()
        except ApiError as exc:
            messages = {
                "csrf_origin_required": "课程 PPT 写入需要可信的本机回环来源。",
                "origin_denied": "课程 PPT 请求来源不受信任。",
                "csrf_fetch_site_denied": "课程 PPT 不接受跨站写入。",
            }
            raise ApiError(
                exc.code,
                messages.get(exc.code, "课程 PPT 写入来源校验失败。"),
                exc.status,
                dict(exc.details),
            ) from exc

    def _require_presentation_read_origin(self) -> None:
        try:
            self._require_safe_browser_read_origin()
        except ApiError as exc:
            messages = {
                "origin_required": "课程 PPT 读取需要可信的本机回环来源。",
                "origin_denied": "课程 PPT 请求来源不受信任。",
                "fetch_site_denied": "课程 PPT 不接受跨站读取。",
            }
            raise ApiError(
                exc.code,
                messages.get(exc.code, "课程 PPT 读取来源校验失败。"),
                exc.status,
                dict(exc.details),
            ) from exc

    def _require_safe_browser_write_origin(self) -> None:
        origin = self.headers.get("Origin")
        if not origin:
            raise ApiError(
                "csrf_origin_required",
                "local settings writes require a trusted loopback Origin",
                403,
            )
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not self._origin_allowed(origin)
        ):
            raise ApiError("origin_denied", "origin is not allowed", 403)
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
            raise ApiError("csrf_fetch_site_denied", "cross-site write denied", 403)

    def _require_safe_browser_read_origin(self) -> None:
        origin = self.headers.get("Origin")
        if not origin:
            # Browsers normally omit Origin on same-origin GET fetches.  Accept
            # only the browser-equivalent proof: exact same-origin Fetch
            # Metadata plus a trusted loopback Referer.  Bare API clients still
            # have to send Origin explicitly and remain rejected otherwise.
            fetch_site = self.headers.get("Sec-Fetch-Site")
            referer = self.headers.get("Referer")
            parsed_referer = urlsplit(referer) if referer else None
            referer_origin = (
                f"{parsed_referer.scheme}://{parsed_referer.netloc}"
                if parsed_referer is not None
                else ""
            )
            if (
                fetch_site != "same-origin"
                or parsed_referer is None
                or parsed_referer.scheme != "http"
                or parsed_referer.hostname not in {"127.0.0.1", "localhost", "::1"}
                or not self._origin_allowed(referer_origin)
            ):
                raise ApiError(
                    "origin_required",
                    "evidence retrieval requires a trusted loopback Origin",
                    403,
                )
            return
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not self._origin_allowed(origin)
        ):
            raise ApiError("origin_denied", "origin is not allowed", 403)
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site and fetch_site not in {"same-origin", "same-site", "none"}:
            raise ApiError(
                "fetch_site_denied", "cross-site evidence retrieval denied", 403
            )

    def _read_json_body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(
                "chunked_requests_not_supported", "chunked requests are rejected", 411
            )
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ApiError("content_length_required", "Content-Length is required", 411)
        try:
            length = int(content_length)
        except ValueError as exc:
            raise ApiError("invalid_content_length", "invalid Content-Length") from exc
        if length < 0 or length > self.server.config.max_request_bytes:
            raise ApiError("request_too_large", "request exceeds configured limit", 413)
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise ApiError(
                "unsupported_content_type", "application/json is required", 415
            )
        raw = self.rfile.read(length)
        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError(f"duplicate JSON key: {key}")
                value[key] = item
            return value

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite JSON number is forbidden: {value}")

        try:
            body = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=unique_object,
                parse_constant=reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ApiError(
                "invalid_json", "request body is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(body, dict):
            raise ApiError("invalid_json", "request body must be a JSON object")
        return body

    def _read_intake_upload_body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise ApiError(
                "chunked_requests_not_supported", "chunked requests are rejected", 411
            )
        if self.headers.get("Content-Encoding"):
            raise ApiError(
                "content_encoding_unsupported",
                "encoded upload bodies are rejected",
                415,
            )
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            raise ApiError("content_length_required", "Content-Length is required", 411)
        try:
            length = int(content_length)
        except ValueError as exc:
            raise ApiError("invalid_content_length", "invalid Content-Length") from exc
        maximum = min(
            self.server.config.max_request_bytes,
            self.server.config.max_upload_bytes,
        )
        if length < 1 or length > maximum:
            raise ApiError("request_too_large", "upload exceeds configured limit", 413)
        content_type = self.headers.get("Content-Type", "")
        if ";" in content_type or content_type not in {
            "image/png",
            "image/jpeg",
            "image/webp",
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        }:
            raise ApiError("unsupported_content_type", "upload Content-Type is not supported", 415)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ApiError("upload_truncated", "upload body is incomplete", 400)
        return {
            "_intake_upload_bytes": raw,
            "_intake_upload_content_type": content_type,
        }

    def _serve_static(self, request_id: str, path: str) -> int:
        if self.command != "GET":
            self._send_error(
                request_id, ApiError("method_not_allowed", "method not allowed", 405)
            )
            return 405
        relative = (
            "index.html"
            if path in {"", "/", "/overlay", "/overlay/"}
            else path.lstrip("/")
        )
        relative = relative.removeprefix("overlay/")
        frozen = self.server.service.frozen_browse
        if frozen is not None:
            try:
                payload = frozen.static(relative)
            except Exception as exc:  # noqa: BLE001 - frozen reader sanitizes errors
                code = getattr(exc, "code", "browse_snapshot_static_not_found")
                error_status = getattr(exc, "status", 404)
                if not isinstance(code, str) or not code:
                    code = "browse_snapshot_static_not_found"
                if isinstance(error_status, bool) or not isinstance(error_status, int):
                    error_status = 404
                self._send_error(
                    request_id,
                    ApiError(
                        code,
                        "frozen workbench static asset is unavailable",
                        error_status,
                    ),
                )
                return error_status
            data = payload.data
            self.send_response(200)
            self._common_headers(request_id)
            self.send_header("Content-Type", payload.content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return 200
        try:
            file_path = safe_join(self.server.config.overlay_root, relative)
        except SecurityError as exc:
            self._send_error(request_id, ApiError(exc.code, str(exc), exc.status))
            return exc.status
        if not file_path.is_file():
            self._send_error(
                request_id, ApiError("not_found", "static file not found", 404)
            )
            return 404
        content_type = (
            mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        )
        data = file_path.read_bytes()
        self.send_response(200)
        self._common_headers(request_id)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return 200

    def _send_json(
        self, request_id: str, status: int, *, data: dict[str, Any] | None = None
    ) -> None:
        envelope = {
            "contract_version": CONTRACT_VERSION,
            "request_id": request_id,
            "data": data or {},
        }
        raw = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self._common_headers(request_id)
        self._origin_header()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_error(self, request_id: str, error: ApiError) -> None:
        envelope = {
            "contract_version": CONTRACT_VERSION,
            "request_id": request_id,
            "error": {
                "code": error.code,
                "message": str(error),
                "details": error.details,
            },
        }
        raw = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(error.status)
        self._common_headers(request_id)
        self._origin_header()
        if error.status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="shchem-gateway"')
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_binary(
        self,
        request_id: str,
        path: Path,
        content_type: str,
        *,
        download_name: str | None = None,
        fallback_name: str | None = None,
    ) -> int:
        data = path.read_bytes()
        visible_name = download_name or path.name
        return self._send_download_bytes(
            request_id,
            data,
            content_type,
            download_name=visible_name,
            fallback_name=fallback_name,
        )

    def _send_download_bytes(
        self,
        request_id: str,
        data: bytes,
        content_type: str,
        *,
        download_name: str,
        fallback_name: str | None = None,
    ) -> int:
        visible_name = download_name
        ascii_fallback = fallback_name or re.sub(
            r"[^A-Za-z0-9._-]+", "_", visible_name
        ).strip("._")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", ascii_fallback):
            ascii_fallback = "download.bin"
        self.send_response(200)
        self._common_headers(request_id)
        self._origin_header()
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quote(visible_name, safe="")}',
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return 200

    def _send_memory_binary(
        self, request_id: str, payload: CandidateCropPayload
    ) -> int:
        # The reader has already bounded, parsed and hash-verified this exact
        # buffer.  Do not reopen a path or derive a filename here.
        data = payload.data
        self.send_response(200)
        self._common_headers(request_id)
        self._origin_header()
        self.send_header("Content-Type", payload.content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return 200

    def _common_headers(self, request_id: str) -> None:
        self.send_header("X-Request-ID", request_id)
        self.send_header("X-Content-Type-Options", "nosniff")
        # Same-origin GET fetches omit Origin; retain a same-origin-only
        # Referer so the read-origin guard can authenticate the loopback page.
        # Browsers still send no Referer to any cross-origin destination.
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'self' http://127.0.0.1:* http://localhost:*",
        )

    def _origin_allowed(self, origin: str) -> bool:
        if origin in self.server.config.allowed_origins:
            return True
        allowed = {
            f"http://127.0.0.1:{self.server.server_address[1]}",
            f"http://localhost:{self.server.server_address[1]}",
        }
        return origin in allowed

    def _origin_header(self) -> None:
        origin = self.headers.get("Origin")
        if origin and self._origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")


def create_server(config: AppConfig) -> GatewayHTTPServer:
    config.validate()
    return GatewayHTTPServer((config.bind_host, config.port), config)
