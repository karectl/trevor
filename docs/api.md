---
icon: lucide/plug
---

# API Reference

All endpoints require authentication unless noted. In dev mode (`DEV_AUTH_BYPASS=true`), send `Authorization: Bearer admin-token` for admin access, or omit for researcher access.

## Health

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/health` | None | Liveness/readiness probe |

## Users

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/users/me` | Any | Current user + memberships + realm roles |

## Projects

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/projects` | Any | List all projects |
| `GET` | `/projects/{id}` | Any | Get project by ID |

## Memberships

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/memberships/project/{id}` | Any | List memberships for project |
| `POST` | `/memberships` | `tre_admin` | Create membership (role conflict validated) |
| `DELETE` | `/memberships/{id}` | `tre_admin` | Remove membership |

## Requests

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/requests` | Researcher | Create airlock request |
| `GET` | `/requests` | Any | List requests (filtered by membership) |
| `GET` | `/requests/{id}` | Member/Admin | Get request with objects |
| `POST` | `/requests/{id}/submit` | Owner/Admin | Submit request (enqueues agent review) |
| `POST` | `/requests/{id}/resubmit` | Owner/Admin | Resubmit after changes |

## Output Objects

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/requests/{id}/objects` | Researcher | Upload output object |
| `GET` | `/requests/{id}/objects` | Member/Admin | List objects |
| `GET` | `/requests/{id}/objects/{oid}` | Member/Admin | Get object |
| `PATCH` | `/requests/{id}/objects/{oid}/metadata` | Researcher | Update metadata |
| `GET` | `/requests/{id}/objects/{oid}/metadata` | Member/Admin | Get metadata |
| `POST` | `/requests/{id}/objects/{oid}/replace` | Researcher | Upload replacement object |
| `GET` | `/requests/{id}/objects/{oid}/versions` | Member/Admin | List object version history |

## Reviews

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/requests/{id}/reviews` | Member/Admin | List reviews |
| `GET` | `/requests/{id}/reviews/{rid}` | Member/Admin | Get single review |
| `POST` | `/requests/{id}/reviews` | Checker/Admin | Submit human review |

## Release

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/requests/{id}/release` | `tre_admin` | Trigger release (RO-Crate assembly, egress) |
| `GET` | `/requests/{id}/release` | Member/Admin | Get release record |

## Ingress Delivery

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `POST` | `/requests/{id}/objects/{oid}/upload-url` | Admin/Senior | Generate pre-signed PUT URL for external upload |
| `POST` | `/requests/{id}/objects/{oid}/confirm-upload` | Admin/Senior | Confirm upload, compute checksum |
| `POST` | `/requests/{id}/deliver` | `tre_admin` | Deliver approved ingress to workspace (pre-signed GET URLs) |
| `GET` | `/requests/{id}/delivery` | Member/Admin | Get delivery record |

## Notifications

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/notifications/unread-count` | Any | Unread notification count (JSON or SSE signals for Datastar badge) |
| `GET` | `/notifications` | Any | List notifications for current user (newest first, paginated via `limit` + `before`) |
| `PATCH` | `/notifications/{id}/read` | Any | Mark a single notification as read |
| `POST` | `/notifications/mark-all-read` | Any | Mark all notifications as read |

### Query parameters for `GET /notifications`

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | int | 20 | Max results (1–100) |
| `before` | datetime | — | Return only notifications older than this timestamp (cursor pagination) |
| `unread_only` | bool | false | Return only unread notifications |

### Notification event types

| Event type | Recipients | Trigger |
|---|---|---|
| `request.submitted` | All checkers on the project | Researcher submits a request |
| `agent_review.ready` | All checkers on the project | Agent review job completes |
| `request.changes_requested` | Submitter | Checker requests changes |
| `request.approved` | Submitter | Request approved by checkers |
| `request.rejected` | Submitter | Request rejected by checkers |
| `request.released` | Submitter | Release job completes |
| `presigned_url.expiring_soon` | Submitter | Download link near expiry |
| `request.stuck` | Admins | Request exceeds SLA threshold |

## Audit

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/requests/{id}/audit` | Member/Admin | List audit events for request |

## Admin

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/admin/requests` | Admin/Senior | All-projects request overview |
| `GET` | `/admin/metrics` | Admin/Senior | Pipeline metrics + stuck detection |
| `GET` | `/admin/audit` | `tre_admin` | Filterable audit log |
| `GET` | `/admin/audit/export` | `tre_admin` | Export audit log as CSV |

## UI (HTML)

All UI routes return HTML via Jinja2 + Datastar. See [UI Guide](ui.md) for details.

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| `GET` | `/ui/requests` | Any | Researcher request list |
| `GET` | `/ui/requests/new` | Any | Create request form |
| `POST` | `/ui/requests` | Researcher | Create request via form |
| `GET` | `/ui/requests/{id}` | Member/Admin | Request detail |
| `GET/POST` | `/ui/requests/{id}/upload` | Researcher | Upload object form |
| `GET/POST` | `/ui/requests/{id}/objects/{oid}/metadata` | Researcher | Metadata form |
| `GET/POST` | `/ui/requests/{id}/objects/{oid}/replace` | Researcher | Replace form |
| `POST` | `/ui/requests/{id}/submit` | Owner/Admin | Submit via UI |
| `POST` | `/ui/requests/{id}/resubmit` | Owner/Admin | Resubmit via UI |
| `POST` | `/ui/requests/{id}/release` | `tre_admin` | Release via UI |
| `GET` | `/ui/ingress/new` | Admin/Senior | Ingress request creation form |
| `POST` | `/ui/requests/ingress` | Admin/Senior | Create ingress request via form |
| `GET/POST` | `/ui/requests/{id}/ingress-upload` | Admin/Senior | Manage ingress object slots |
| `POST` | `/ui/requests/{id}/objects/{oid}/generate-url` | Admin/Senior | Generate upload URL via UI |
| `POST` | `/ui/requests/{id}/objects/{oid}/confirm` | Admin/Senior | Confirm upload via UI |
| `POST` | `/ui/requests/{id}/deliver` | `tre_admin` | Deliver ingress request via UI |
| `GET` | `/ui/review` | Checker/Admin | Review queue |
| `GET/POST` | `/ui/review/{id}` | Checker/Admin | Review form |
| `GET` | `/ui/admin` | `tre_admin` | Admin request overview |
| `GET` | `/ui/admin/metrics` | `tre_admin` | Metrics dashboard |
| `GET` | `/ui/admin/audit` | `tre_admin` | Audit log |
| `GET` | `/ui/admin/memberships/{pid}` | `tre_admin` | Membership management |
| `POST` | `/ui/admin/memberships` | `tre_admin` | Create membership via UI |
| `POST` | `/ui/admin/memberships/{mid}/delete` | `tre_admin` | Delete membership via UI |
| `GET` | `/ui/notifications` | Any | Notification inbox |
| `POST` | `/ui/notifications/{id}/read` | Any | Mark notification read (form POST) |
| `POST` | `/ui/notifications/mark-all-read` | Any | Mark all read (form POST) |
