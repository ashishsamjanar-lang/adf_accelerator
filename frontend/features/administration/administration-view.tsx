"use client";

import { useCallback, useEffect, useState } from "react";
import { Dialog } from "@/components/ui/dialog";
import { Loader2, KeyRound, LogOut, Pencil, Plus, Save, Trash2, X } from "lucide-react";
import type { FeatureProps } from "@/features/types";
import { PageHeader } from "@/components/common/page-header";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { StatusPill } from "@/components/common/status-pill";
import { EmptyState, resourceStatus } from "@/components/common/empty-state";
import { Pagination } from "@/components/common/pagination";
import { usePagination } from "@/lib/use-pagination";
import { cn } from "@/lib/utils";
import { can, ROLE_LABELS, ROLE_DESCRIPTIONS } from "@/lib/state";
import { formatDateTime, timeAgo } from "@/lib/format";
import { api, ApiError } from "@/lib/api/client";
import type { AdminSession, Environment, Role, User } from "@/types";
import { SsoPanel } from "@/features/administration/sso-panel";

export type AdminSection = "overview" | "users" | "teams" | "environments" | "audit";

interface AdministrationViewProps extends FeatureProps {
  section: AdminSection;
}

export function AdministrationView({ data, user, section }: AdministrationViewProps) {
  if (section === "users") return <UsersSection data={data} user={user} />;
  if (section === "teams") return <TeamsSection data={data} />;
  if (section === "environments") return <EnvironmentsSection data={data} user={user} />;
  if (section === "audit") return <AuditSection data={data} />;
  return <OverviewSection data={data} />;
}

function OverviewSection({ data }: { data: FeatureProps["data"] }) {
  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="PLATFORM ADMINISTRATION"
        title="Administration"
        detail="Administrative actions are logged for full traceability."
      />
      <section className="panel">
        <div className="px-5 py-3">
          <p className="m-0 text-sm text-muted">
            Administration overview. Use the top navigation or sidebar to access specific administration areas.
          </p>
        </div>
      </section>
      <div className="section-heading" style={{ marginTop: "34px" }}>
        <div>
          <span className="eyebrow">AUDIT TRAIL</span>
          <h2>Recent administrative events</h2>
        </div>
      </div>
      <section className="panel">
        {data.audit.data.slice(0, 5).map((entry) => (
          <div key={entry.id} className="px-5 py-3 text-xs" style={{ borderBottom: "1px solid var(--line)" }}>
            <div className="flex items-center justify-between">
              <div>
                <strong className="text-ink">{entry.action}</strong>
                <span className="ml-2 text-muted">— {entry.target}</span>
              </div>
              <span className="text-xs text-muted">{timeAgo(entry.timestamp)}</span>
            </div>
            <p className="m-0 mt-1 text-xs leading-5 text-muted">
              {entry.details} · by {entry.actorName ?? entry.actor} · {formatDateTime(entry.timestamp)}
            </p>
          </div>
        ))}
      </section>
    </div>
  );
}

/**
 * §30 Admin → Audit: the full append-only trail (read-only — no edit/delete
 * affordance exists by design). Team scoping is server-side (`GET /admin/audit`
 * narrows a TEAM_ADMIN to their own team); this only filters what came back.
 */
function AuditSection({ data }: { data: FeatureProps["data"] }) {
  const [query, setQuery] = useState("");
  const [actionFilter, setActionFilter] = useState("all");
  const allEntries = data.audit.data;
  const actions = Array.from(new Set(allEntries.map((entry) => entry.action))).sort();
  const entries = allEntries.filter((entry) => {
    const q = query.trim().toLowerCase();
    const matchesQuery =
      !q || `${entry.actorName ?? ""} ${entry.actor} ${entry.target} ${entry.details}`.toLowerCase().includes(q);
    return matchesQuery && (actionFilter === "all" || entry.action === actionFilter);
  });
  const { page, setPage, totalPages, totalItems, pageSize, pageItems } = usePagination(entries, {
    resetKey: `${query}|${actionFilter}`,
  });

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="AUDIT TRAIL"
        title="Audit log"
        detail="Append-only. Team admins see their own team's events; platform admins see every team and global events."
      />
      {allEntries.length > 0 ? (
        <div className="catalog-toolbar mb-4">
          <label className="search-box">
            ⌕
            <Input
              placeholder="Search by actor, target, or details"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="border-0 bg-transparent outline-none ring-0 focus:border-0 focus:ring-0"
            />
          </label>
          <Select value={actionFilter} onChange={(e) => setActionFilter(e.target.value)} aria-label="Filter by action" className="w-auto">
            <option value="all">All actions</option>
            {actions.map((action) => (
              <option key={action} value={action}>{action}</option>
            ))}
          </Select>
        </div>
      ) : null}
      <section className="panel catalog-panel">
        {allEntries.length === 0 ? (
          <EmptyState status={resourceStatus(data.audit)} errorMessage={data.audit.error} title="No audit events yet" message="Administrative and pipeline actions appear here as they happen." />
        ) : entries.length === 0 ? (
          <EmptyState title="No events match" message="Try a different search term or action filter." />
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted">
                <th className="px-5 py-2 font-medium">When</th>
                <th className="px-5 py-2 font-medium">Action</th>
                <th className="px-5 py-2 font-medium">Target</th>
                <th className="px-5 py-2 font-medium">Actor</th>
                <th className="px-5 py-2 font-medium">Details</th>
              </tr>
            </thead>
            <tbody>
              {pageItems.map((entry) => (
                <tr key={entry.id} style={{ borderTop: "1px solid var(--line)" }}>
                  <td className="whitespace-nowrap px-5 py-2 text-muted" title={formatDateTime(entry.timestamp)}>{timeAgo(entry.timestamp)}</td>
                  <td className="px-5 py-2"><Badge>{entry.action}</Badge></td>
                  <td className="px-5 py-2 text-ink">{entry.target || "—"}</td>
                  <td className="px-5 py-2">{entry.actorName ?? entry.actor}</td>
                  <td className="px-5 py-2 text-muted">{entry.details || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <Pagination page={page} totalPages={totalPages} totalItems={totalItems} pageSize={pageSize} onPageChange={setPage} itemLabel="events" />
      </section>
    </div>
  );
}

function UsersSection({ data, user: actor }: { data: FeatureProps["data"]; user: User }) {
  const [query, setQuery] = useState("");
  const [roleFilter, setRoleFilter] = useState<Role | "all">("all");
  const [creating, setCreating] = useState(false);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [resettingUser, setResettingUser] = useState<User | null>(null);
  const [revealedPassword, setRevealedPassword] = useState<RevealedCredentials | null>(null);

  // §11: onboarding a new account is PLATFORM_ADMIN only; editing an
  // existing user (role/team/active) is also open to TEAM_ADMIN, scoped
  // server-side to their own team (`app/api/users.py`) — this mirrors that,
  // it's UX only, the backend is the real gate.
  const canCreate = can(actor.role, "admin:platform");
  const canManage = (target: User) =>
    can(actor.role, "admin:platform") || (actor.role === "TEAM_ADMIN" && target.teamId === actor.teamId);

  const allUsers = data.users.data;
  const users = allUsers.filter((user) => {
    const q = query.trim().toLowerCase();
    const matchesQuery = !q || `${user.name} ${user.email} ${user.username ?? ""}`.toLowerCase().includes(q);
    const matchesRole = roleFilter === "all" || user.role === roleFilter;
    return matchesQuery && matchesRole;
  });

  const { page, setPage, totalPages, totalItems, pageSize, pageItems } = usePagination(users, {
    resetKey: `${query}|${roleFilter}`,
  });

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="USER ACCESS"
        title="Users"
        detail="Access is enforced server-side (RBAC role + team scope)."
        action={
          <Button disabled={!canCreate} title={canCreate ? undefined : "Requires platform admin"} onClick={() => setCreating(true)}>
            <Plus className="h-3.5 w-3.5" />
            Add user
          </Button>
        }
      />
      {allUsers.length > 0 ? (
        <div className="catalog-toolbar mb-4">
          <label className="search-box">
            ⌕
            <Input
              placeholder="Search users by name or email"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="border-0 bg-transparent outline-none ring-0 focus:border-0 focus:ring-0"
            />
          </label>
          <Select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value as Role | "all")} aria-label="Filter by role" className="w-auto">
            <option value="all">All roles</option>
            {Object.entries(ROLE_LABELS).map(([role, label]) => (
              <option key={role} value={role}>{label}</option>
            ))}
          </Select>
        </div>
      ) : null}
      <section className="panel catalog-panel">
        {allUsers.length === 0 ? (
          <EmptyState status={resourceStatus(data.users)} errorMessage={data.users.error} title="No users yet" message="Add a user to onboard them with a role and team." />
        ) : users.length === 0 ? (
          <EmptyState title="No users match" message="Try a different search term or role filter." />
        ) : (
          <div className="divide-y divide-line">
            {pageItems.map((user) => (
              <div key={user.id} className="project-row" style={{ gridTemplateColumns: "34px minmax(0,1fr) 110px 120px auto" }}>
                <span className="project-mark">
                  <span className="avatar">{initials(user.name)}</span>
                </span>
                <div>
                  <strong>{user.name}</strong>
                  <span>
                    {user.username ? `${user.username} · ` : ""}
                    {user.email}
                  </span>
                </div>
                <span className="text-xs text-muted">{user.teamId}</span>
                <StatusPill tone="forge">{ROLE_LABELS[user.role]}</StatusPill>
                <div className="flex items-center justify-end gap-1.5">
                  {!user.active ? <Badge variant="amber">Inactive</Badge> : null}
                  {canManage(user) ? (
                    <>
                      <Button variant="ghost" size="icon" aria-label={`Edit ${user.name}`} onClick={() => setEditingUser(user)}>
                        <Pencil className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label={`Reset password for ${user.name}`}
                        onClick={() => setResettingUser(user)}
                      >
                        <KeyRound className="h-3.5 w-3.5" />
                      </Button>
                    </>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
        <Pagination
          page={page}
          totalPages={totalPages}
          totalItems={totalItems}
          pageSize={pageSize}
          onPageChange={setPage}
          itemLabel="users"
        />
      </section>
      {canCreate || actor.role === "TEAM_ADMIN" ? <SessionsPanel /> : null}
      {canCreate ? <SsoPanel teams={data.teams.data} /> : null}
      <div className="mt-6 space-y-2 text-xs text-muted">
        {Object.entries(ROLE_DESCRIPTIONS).map(([role, desc]) => (
          <p key={role}>
            <strong className="text-ink">{ROLE_LABELS[role as keyof typeof ROLE_LABELS]}</strong> — {desc}
          </p>
        ))}
      </div>

      {creating ? (
        <NewUserDialog
          teams={data.teams.data}
          onClose={() => setCreating(false)}
          onCreated={(created) => {
            setCreating(false);
            setRevealedPassword({ email: created.email, username: created.username, password: created.tempPassword });
            data.users.reload();
          }}
        />
      ) : null}
      {editingUser ? (
        <EditUserDialog
          target={editingUser}
          actor={actor}
          teams={data.teams.data}
          onClose={() => setEditingUser(null)}
          onSaved={() => {
            setEditingUser(null);
            data.users.reload();
          }}
        />
      ) : null}
      {resettingUser ? (
        <ResetPasswordDialog
          target={resettingUser}
          onClose={() => setResettingUser(null)}
          onReset={(tempPassword) => {
            setResettingUser(null);
            setRevealedPassword({ email: resettingUser.email, username: resettingUser.username, password: tempPassword });
          }}
        />
      ) : null}
      {revealedPassword ? (
        <TempPasswordDialog revealed={revealedPassword} onClose={() => setRevealedPassword(null)} />
      ) : null}
    </div>
  );
}

/**
 * §27 session admin — live sessions (unrevoked, not idle- or absolute-expired), team-scoped
 * server-side for TEAM_ADMIN. Revoking takes effect on that session's next request. Always empty
 * under AUTH_MODE=mock, where no sessions are issued.
 */
export function SessionsPanel() {
  const [sessions, setSessions] = useState<AdminSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);

  const load = useCallback(() => {
    api.admin
      .sessions()
      .then((rows) => {
        setSessions(rows);
        setError(null);
      })
      .catch((err) => {
        setSessions([]);
        setError(err instanceof Error ? err.message : "Could not load sessions.");
      });
  }, []);
  useEffect(load, [load]);

  async function revoke(handle: string) {
    setRevoking(handle);
    try {
      await api.admin.revokeSession(handle);
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not revoke the session.");
    } finally {
      setRevoking(null);
    }
  }

  return (
    <section className="panel catalog-panel mt-6">
      <div className="px-4 pt-4">
        <h2 className="text-sm font-semibold text-ink">Active sessions</h2>
        <p className="text-xs text-muted">Signed-in sessions (password mode). Revoking signs that browser out on its next request.</p>
      </div>
      {error ? <Alert className="mx-4 mt-3" variant="error" description={error} /> : null}
      {sessions === null ? (
        <EmptyState status="loading" title="Loading sessions" message="" />
      ) : sessions.length === 0 ? (
        <EmptyState title="No active sessions" message="Sessions appear here once users sign in with AUTH_MODE=password." />
      ) : (
        <div className="divide-y divide-line">
          {sessions.map((s) => (
            <div key={s.handle} className="project-row" style={{ gridTemplateColumns: "minmax(0,1fr) 160px 160px auto" }}>
              <div>
                <strong>{s.userName ?? s.userId}</strong>
                <span>{[s.userEmail, s.ip].filter(Boolean).join(" · ")}</span>
              </div>
              <span className="text-xs text-muted">Signed in {s.issuedAt ? timeAgo(s.issuedAt) : "—"}</span>
              <span className="text-xs text-muted">Idle expiry {s.idleExpiresAt ? formatDateTime(s.idleExpiresAt) : "—"}</span>
              <div className="flex items-center justify-end gap-1.5">
                {s.current ? <Badge variant="amber">This session</Badge> : null}
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={s.current || revoking === s.handle}
                  title={s.current ? "Use Sign out to end your own session" : undefined}
                  aria-label={`Revoke session for ${s.userName ?? s.userId}`}
                  onClick={() => void revoke(s.handle)}
                >
                  <LogOut className="h-3.5 w-3.5" />
                  Revoke
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function NewUserDialog({
  teams,
  onClose,
  onCreated,
}: {
  teams: FeatureProps["data"]["teams"]["data"];
  onClose: () => void;
  onCreated: (created: User & { tempPassword: string | null }) => void;
}) {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  // Prefilled from the email's local part until the admin types their own.
  const [usernameTouched, setUsernameTouched] = useState(false);
  const [choosePassword, setChoosePassword] = useState(false);
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("DEVELOPER");
  const [teamId, setTeamId] = useState(teams[0]?.id ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || !email.trim() || !username.trim()) return;
    setSaving(true);
    setError(undefined);
    try {
      const created = await api.admin.createUser({
        name: name.trim(),
        email: email.trim(),
        username: username.trim(),
        password: choosePassword ? password : undefined,
        role,
        teamId: teamId || null,
      });
      onCreated(created);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create the user.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      eyebrow="USER ACCESS"
      title="Add user"
      description="Onboards a new account that signs in with a username and password — there is no self-service sign-up."
    >
      <form onSubmit={handleSubmit}>
        {error ? <Alert variant="error" description={error} className="mb-4" /> : null}
        <div className="form-fields">
          <label>
            <span>Full name *</span>
            <Input value={name} onChange={(e) => setName(e.target.value)} autoFocus required />
          </label>
          <label>
            <span>Email *</span>
            <Input
              type="email"
              value={email}
              onChange={(e) => {
                setEmail(e.target.value);
                if (!usernameTouched) setUsername(usernameFromEmail(e.target.value));
              }}
              required
            />
          </label>
          <label>
            <span>Username *</span>
            <Input
              value={username}
              onChange={(e) => {
                setUsernameTouched(true);
                setUsername(e.target.value.toLowerCase());
              }}
              title="2-64 characters: lowercase letters, digits, '.', '_' or '-'"
              autoComplete="off"
              required
            />
          </label>
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={choosePassword} onChange={(e) => setChoosePassword(e.target.checked)} />
            <span>Set the initial password myself (otherwise a one-time password is generated)</span>
          </label>
          {choosePassword ? (
            <label>
              <span>Initial password *</span>
              <Input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                minLength={8}
                required
              />
            </label>
          ) : null}
          <label>
            <span>Role *</span>
            <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {Object.entries(ROLE_LABELS).map(([r, label]) => (
                <option key={r} value={r}>{label}</option>
              ))}
            </Select>
          </label>
          <label>
            <span>Team</span>
            <Select value={teamId} onChange={(e) => setTeamId(e.target.value)}>
              <option value="">No team</option>
              {teams.map((team) => (
                <option key={team.id} value={team.id}>{team.name}</option>
              ))}
            </Select>
          </label>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            type="submit"
            disabled={saving || !name.trim() || !email.trim() || !username.trim() || (choosePassword && !password)}
          >
            {saving ? "Creating…" : "Create user"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function EditUserDialog({
  target,
  actor,
  teams,
  onClose,
  onSaved,
}: {
  target: User;
  actor: User;
  teams: FeatureProps["data"]["teams"]["data"];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [role, setRole] = useState<Role>(target.role);
  const [teamId, setTeamId] = useState(target.teamId ?? "");
  const [active, setActive] = useState(target.active);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  // A TEAM_ADMIN may never grant PLATFORM_ADMIN or move a user to a
  // different team (app/api/users.py enforces this too — this is UX only).
  const isPlatformAdmin = actor.role === "PLATFORM_ADMIN";

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(undefined);
    try {
      // Backend treats an omitted `teamId` as "leave unchanged" (§27
      // extension) — an empty string isn't a valid team id, so "No team"
      // here can't yet clear an assignment, only leave the current one.
      await api.admin.updateUser(target.id, {
        role,
        teamId: isPlatformAdmin && teamId ? teamId : undefined,
        active,
      });
      onSaved();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to update this user.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onClose={onClose} eyebrow="USER ACCESS" title={`Edit ${target.name}`} description={target.email}>
      <form onSubmit={handleSubmit}>
        {error ? <Alert variant="error" description={error} className="mb-4" /> : null}
        <div className="form-fields">
          <label>
            <span>Role</span>
            <Select value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {Object.entries(ROLE_LABELS)
                .filter(([r]) => isPlatformAdmin || r !== "PLATFORM_ADMIN")
                .map(([r, label]) => (
                  <option key={r} value={r}>{label}</option>
                ))}
            </Select>
          </label>
          <label>
            <span>Team</span>
            <Select value={teamId} onChange={(e) => setTeamId(e.target.value)} disabled={!isPlatformAdmin}>
              <option value="">No team</option>
              {teams.map((team) => (
                <option key={team.id} value={team.id}>{team.name}</option>
              ))}
            </Select>
          </label>
          <label className="flex-row items-center gap-2">
            <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
            <span>Active</span>
          </label>
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          <Button type="submit" disabled={saving}>{saving ? "Saving…" : "Save changes"}</Button>
        </div>
      </form>
    </Dialog>
  );
}

function ResetPasswordDialog({
  target,
  onClose,
  onReset,
}: {
  target: User;
  onClose: () => void;
  onReset: (tempPassword: string) => void;
}) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  async function handleConfirm() {
    setSaving(true);
    setError(undefined);
    try {
      const { tempPassword } = await api.admin.resetPassword(target.id);
      onReset(tempPassword);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to reset this user's password.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open onClose={onClose} eyebrow="USER ACCESS" title="Reset password" description={`${target.name} · ${target.email}`}>
      {error ? <Alert variant="error" description={error} className="mb-4" /> : null}
      <p className="text-xs text-muted">
        Generates a new one-time temporary password and signs this user out of every existing session.
        They must set a new password on their next login.
      </p>
      <div className="mt-5 flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
        <Button variant="danger" onClick={handleConfirm} disabled={saving}>
          {saving ? "Resetting…" : "Reset password"}
        </Button>
      </div>
    </Dialog>
  );
}

/** Mirrors the backend's `username_from_email` (app/security.py) — a prefill only; the server validates. */
function usernameFromEmail(email: string): string {
  const local = (email.split("@")[0] ?? "").trim().toLowerCase();
  return local.replace(/[^a-z0-9._-]/g, ".").replace(/^[._-]+|[._-]+$/g, "").slice(0, 64);
}

/** `password` is null when the admin chose the initial password themselves — nothing to reveal. */
type RevealedCredentials = { email: string; username?: string | null; password: string | null };

function TempPasswordDialog({
  revealed,
  onClose,
}: {
  revealed: RevealedCredentials;
  onClose: () => void;
}) {
  const signIn = revealed.username ? `Sign-in username: ${revealed.username}. ` : "";
  if (revealed.password === null) {
    return (
      <Dialog open onClose={onClose} eyebrow="USER ACCESS" title="User created" description={revealed.email}>
        <Alert
          variant="success"
          description={`${signIn}Share the password you set out of band — they must change it on first login.`}
        />
        <div className="mt-5 flex justify-end">
          <Button onClick={onClose}>Done</Button>
        </div>
      </Dialog>
    );
  }
  return (
    <Dialog open onClose={onClose} eyebrow="USER ACCESS" title="One-time temporary password" description={revealed.email}>
      <Alert
        variant="warning"
        description={`${signIn}Shown once — this app has no email sender, so relay it to the user out of band. They must change it on first login.`}
        className="mb-4"
      />
      <div className="flex items-center justify-between gap-3 rounded-md border border-line bg-surface-3 px-3.5 py-2.5">
        <code className="text-sm font-semibold text-ink">{revealed.password}</code>
      </div>
      <div className="mt-5 flex justify-end">
        <Button onClick={onClose}>Done</Button>
      </div>
    </Dialog>
  );
}

function TeamsSection({ data }: { data: FeatureProps["data"] }) {
  const [query, setQuery] = useState("");
  const allTeams = data.teams.data;
  const teams = allTeams.filter((team) => {
    const q = query.trim().toLowerCase();
    return !q || `${team.name} ${team.description}`.toLowerCase().includes(q);
  });
  const { page, setPage, totalPages, totalItems, pageSize, pageItems } = usePagination(teams, { resetKey: query });

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="TEAM CONTEXT"
        title="Teams"
        detail="Team isolation is enforced in application queries and authorization checks."
      />
      {allTeams.length > 0 ? (
        <div className="catalog-toolbar mb-4">
          <label className="search-box">
            ⌕
            <Input
              placeholder="Search teams"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="border-0 bg-transparent outline-none ring-0 focus:border-0 focus:ring-0"
            />
          </label>
        </div>
      ) : null}
      {allTeams.length === 0 ? (
        <div className="panel">
          <EmptyState status={resourceStatus(data.teams)} errorMessage={data.teams.error} title="No teams yet" message="Teams group users and projects so access and ownership stay explicit." />
        </div>
      ) : teams.length === 0 ? (
        <div className="panel">
          <EmptyState title="No teams match" message="Try a different search term." />
        </div>
      ) : (
        <>
          <div className="entity-grid">
            {pageItems.map((team) => (
              <article className="panel entity-card" key={team.id}>
                <span className="entity-avatar">
                  <span className="avatar">{initials(team.name)}</span>
                </span>
                <h2>{team.name}</h2>
                <p>{team.description}</p>
                <div className="mt-auto flex w-full items-center justify-between">
                  <span className="text-xs text-muted">
                    {team.memberCount} members · {team.projectCount} projects
                  </span>
                  <Badge variant="blue">Active</Badge>
                </div>
              </article>
            ))}
          </div>
          <Pagination
            page={page}
            totalPages={totalPages}
            totalItems={totalItems}
            pageSize={pageSize}
            onPageChange={setPage}
            itemLabel="teams"
            className="mt-2"
          />
        </>
      )}
    </div>
  );
}

const ENV_STATUSES: Environment["status"][] = ["not_configured", "connected", "approval_gated"];

interface EnvironmentDraft {
  label: string;
  status: Environment["status"];
  description: string;
  storageAccount: string;
  sqlServer: string;
  database: string;
  keyVault: string;
  factoryName: string;
  subscriptionId: string;
  resourceGroup: string;
}

/** Config fields required before an environment's connection details can be saved. */
const REQUIRED_ENV_FIELDS = [
  "storageAccount",
  "sqlServer",
  "database",
  "keyVault",
  "factoryName",
  "subscriptionId",
  "resourceGroup",
] as const satisfies readonly (keyof EnvironmentDraft)[];

type EnvironmentFormErrors = Partial<Record<(typeof REQUIRED_ENV_FIELDS)[number] | "name", string>>;

function validateEnvironmentDraft(draft: EnvironmentDraft): EnvironmentFormErrors {
  const errors: EnvironmentFormErrors = {};
  for (const field of REQUIRED_ENV_FIELDS) {
    if (!draft[field].trim()) errors[field] = "Required";
  }
  return errors;
}

function draftFrom(environment: Environment): EnvironmentDraft {
  return {
    label: environment.label,
    status: environment.status,
    description: environment.description,
    storageAccount: environment.configuration.storageAccount,
    sqlServer: environment.configuration.sqlServer,
    database: environment.configuration.database,
    keyVault: environment.configuration.keyVault,
    factoryName: environment.configuration.factoryName ?? "",
    subscriptionId: environment.configuration.subscriptionId ?? "",
    resourceGroup: environment.configuration.resourceGroup ?? "",
  };
}

function emptyEnvironmentDraft(): EnvironmentDraft {
  return {
    label: "",
    status: "not_configured",
    description: "",
    storageAccount: "",
    sqlServer: "",
    database: "",
    keyVault: "",
    factoryName: "",
    subscriptionId: "",
    resourceGroup: "",
  };
}

/** Sentinel `editing` value for the "add a new ADF instance" card (distinct from any real name). */
const NEW_ENV_SENTINEL = "__new__";

function EnvironmentsSection({ data, user }: { data: FeatureProps["data"]; user: FeatureProps["user"] }) {
  const canEdit = user.role === "PLATFORM_ADMIN" || user.role === "TEAM_ADMIN";
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<EnvironmentDraft | null>(null);
  const [newName, setNewName] = useState("");
  const [errors, setErrors] = useState<EnvironmentFormErrors>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string>();
  const [deleting, setDeleting] = useState<string | null>(null);

  function startEdit(environment: Environment) {
    setEditing(environment.name);
    setDraft(draftFrom(environment));
    setErrors({});
    setSaveError(undefined);
  }

  function startCreate() {
    setEditing(NEW_ENV_SENTINEL);
    setDraft(emptyEnvironmentDraft());
    setNewName("");
    setErrors({});
    setSaveError(undefined);
  }

  function cancelEdit() {
    setEditing(null);
    setDraft(null);
    setNewName("");
    setErrors({});
    setSaveError(undefined);
  }

  async function save(environment: Environment) {
    if (!draft) return;
    const validationErrors = validateEnvironmentDraft(draft);
    setErrors(validationErrors);
    if (Object.keys(validationErrors).length > 0) return;
    setSaving(true);
    setSaveError(undefined);
    try {
      // Real PUT /api/v1/environments/{name} — replaces the whole connection
      // config (IMPLEMENTATION_PLAN.md A-4). Values here are identifiers and
      // references only; a raw secret must never be typed into this form.
      await api.environments.update(environment.name, {
        name: environment.name,
        label: draft.label || environment.name,
        status: draft.status,
        description: draft.description,
        storageAccount: draft.storageAccount,
        sqlServer: draft.sqlServer,
        database: draft.database,
        keyVault: draft.keyVault,
        factoryName: draft.factoryName,
        subscriptionId: draft.subscriptionId,
        resourceGroup: draft.resourceGroup,
      });
      data.environments.reload();
      cancelEdit();
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Unable to save this environment.");
    } finally {
      setSaving(false);
    }
  }

  async function saveNew() {
    if (!draft) return;
    const validationErrors: EnvironmentFormErrors = { ...validateEnvironmentDraft(draft) };
    const trimmedName = newName.trim();
    if (!trimmedName) {
      validationErrors.name = "Required";
    } else if (data.environments.data.some((e) => e.name.toLowerCase() === trimmedName.toLowerCase())) {
      validationErrors.name = "An ADF instance with this name already exists.";
    }
    setErrors(validationErrors);
    if (Object.keys(validationErrors).length > 0) return;
    setSaving(true);
    setSaveError(undefined);
    try {
      // Real POST /api/v1/environments — registers a new ADF instance beyond
      // the always-seeded DEV/QA/PROD (multi-instance support). Values here
      // are identifiers and references only; a raw secret must never be
      // typed into this form.
      await api.environments.create({
        name: trimmedName,
        label: draft.label || trimmedName,
        status: draft.status,
        description: draft.description,
        storageAccount: draft.storageAccount,
        sqlServer: draft.sqlServer,
        database: draft.database,
        keyVault: draft.keyVault,
        factoryName: draft.factoryName,
        subscriptionId: draft.subscriptionId,
        resourceGroup: draft.resourceGroup,
      });
      data.environments.reload();
      cancelEdit();
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Unable to create this ADF instance.");
    } finally {
      setSaving(false);
    }
  }

  async function remove(environment: Environment) {
    if (!window.confirm(`Remove ADF instance "${environment.name}"? Any pipeline still targeting it falls back to placeholder tokens.`)) {
      return;
    }
    setDeleting(environment.name);
    setSaveError(undefined);
    try {
      await api.environments.remove(environment.name);
      data.environments.reload();
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Unable to remove this ADF instance.");
    } finally {
      setDeleting(null);
    }
  }

  return (
    <div className="animate-fade-in">
      <PageHeader
        eyebrow="ADF INSTANCE CONFIGURATION"
        title="Environments"
        detail="Each entry is one registered ADF instance a pipeline can target. Values here are identifiers and references only — secrets are stored in Azure Key Vault and never in this application."
        action={
          canEdit && editing === null ? (
            <Button size="sm" onClick={startCreate}>
              <Plus className="h-3.5 w-3.5" />Add ADF instance
            </Button>
          ) : undefined
        }
      />
      {Object.keys(errors).length > 0 && (
        <Alert
          variant="error"
          title="Validation error"
          description="Please fill in every connection field before saving."
          className="mb-4"
        />
      )}
      {saveError && <Alert variant="error" title="Save failed" description={saveError} className="mb-4" />}
      <div className="grid gap-4 lg:grid-cols-3">
        {editing === NEW_ENV_SENTINEL && draft && (
          <EnvironmentCreateCard
            name={newName}
            setName={setNewName}
            draft={draft}
            setDraft={setDraft}
            errors={errors}
            saving={saving}
            onCancel={cancelEdit}
            onSave={saveNew}
          />
        )}
        {data.environments.data.map((environment) =>
          canEdit && editing === environment.name && draft ? (
            <EnvironmentEditCard
              key={environment.name}
              environment={environment}
              draft={draft}
              setDraft={setDraft}
              errors={errors}
              saving={saving}
              onCancel={cancelEdit}
              onSave={() => save(environment)}
            />
          ) : (
            <EnvironmentViewCard
              key={environment.name}
              environment={environment}
              canEdit={canEdit}
              deleting={deleting === environment.name}
              onEdit={() => startEdit(environment)}
              onDelete={() => remove(environment)}
            />
          ),
        )}
      </div>
    </div>
  );
}

function EnvironmentViewCard({
  environment,
  canEdit,
  deleting,
  onEdit,
  onDelete,
}: {
  environment: Environment;
  canEdit: boolean;
  deleting: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <section className="panel" style={{ padding: "20px" }}>
      <div className="flex items-center justify-between">
        <h2 className="m-0 text-lg text-ink">{environment.name}</h2>
        <StatusPill tone={environment.status === "connected" ? "green" : "blue"}>{environment.status}</StatusPill>
      </div>
      <p className="mb-4 mt-1 text-xs leading-5 text-muted">{environment.description}</p>
      <div className="space-y-2 rounded-md border border-line bg-soft p-3 font-mono text-xs">
        {Object.entries(environment.configuration).map(([key, value]) =>
          key === "name" ? null : (
            <div key={key} className="flex items-center justify-between">
              <span className="text-muted">{key.replace(/([A-Z])/g, " $1").toLowerCase()}</span>
              <span className="text-ink">{value || "—"}</span>
            </div>
          ),
        )}
      </div>
      <p className="mb-0 mt-3 text-xs text-muted">
        Key Vault <code>{environment.configuration.keyVault || "(not set)"}</code> resolves secrets at pipeline
        execution time — never entered here.
      </p>
      {canEdit && (
        <div className="mt-3 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onDelete} disabled={deleting}>
            {deleting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
            Delete
          </Button>
          <Button variant="outline" size="sm" onClick={onEdit}>
            <Pencil className="h-3.5 w-3.5" />Edit
          </Button>
        </div>
      )}
    </section>
  );
}

function EnvironmentCreateCard({
  name,
  setName,
  draft,
  setDraft,
  errors,
  saving,
  onCancel,
  onSave,
}: {
  name: string;
  setName: (name: string) => void;
  draft: EnvironmentDraft;
  setDraft: (draft: EnvironmentDraft) => void;
  errors: EnvironmentFormErrors;
  saving: boolean;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <section className="panel" style={{ padding: "20px" }}>
      <div className="flex items-center justify-between">
        <div className="flex-1">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Instance name, e.g. PROD_EU"
            className={cn("font-mono text-sm", errors.name && "border-red focus:border-red focus:ring-red/20")}
          />
          {errors.name && <small className="text-red text-xs">{errors.name}</small>}
        </div>
        <Select
          value={draft.status}
          onChange={(e) => setDraft({ ...draft, status: e.target.value as Environment["status"] })}
          className="ml-2 h-7 text-xs"
        >
          {ENV_STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </Select>
      </div>
      <div className="mt-3 space-y-2">
        <Input
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          placeholder="Description"
        />
        {ENV_FIELD_ROWS.map(([key, label]) => (
          <div key={key} className="flex items-start gap-2">
            <span className="w-32 shrink-0 pt-1.5 text-xs text-muted">
              {label} <span className="text-red">*</span>
            </span>
            <div className="flex-1">
              <Input
                value={draft[key]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                className={cn("font-mono text-xs", errors[key] && "border-red focus:border-red focus:ring-red/20")}
              />
              {errors[key] && <small className="text-red text-xs">{errors[key]}</small>}
            </div>
          </div>
        ))}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={saving}>
            <X className="h-3.5 w-3.5" />Cancel
          </Button>
          <Button size="sm" onClick={onSave} disabled={saving}>
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            Create
          </Button>
        </div>
      </div>
    </section>
  );
}

const ENV_FIELD_ROWS = [
  ["storageAccount", "Storage account"],
  ["sqlServer", "SQL server"],
  ["database", "Database"],
  ["keyVault", "Key Vault name"],
  ["factoryName", "ADF factory name"],
  ["subscriptionId", "Subscription ID"],
  ["resourceGroup", "Resource group"],
] as const satisfies readonly [keyof EnvironmentDraft, string][];

function EnvironmentEditCard({
  environment,
  draft,
  setDraft,
  errors,
  saving,
  onCancel,
  onSave,
}: {
  environment: Environment;
  draft: EnvironmentDraft;
  setDraft: (draft: EnvironmentDraft) => void;
  errors: EnvironmentFormErrors;
  saving: boolean;
  onCancel: () => void;
  onSave: () => void;
}) {
  return (
    <section className="panel" style={{ padding: "20px" }}>
      <div className="flex items-center justify-between">
        <h2 className="m-0 text-lg text-ink">{environment.name}</h2>
        <Select
          value={draft.status}
          onChange={(e) => setDraft({ ...draft, status: e.target.value as Environment["status"] })}
          className="h-7 text-xs"
        >
          {ENV_STATUSES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </Select>
      </div>
      <div className="mt-3 space-y-2">
        <Input
          value={draft.description}
          onChange={(e) => setDraft({ ...draft, description: e.target.value })}
          placeholder="Description"
        />
        {ENV_FIELD_ROWS.map(([key, label]) => (
          <div key={key} className="flex items-start gap-2">
            <span className="w-32 shrink-0 pt-1.5 text-xs text-muted">
              {label} <span className="text-red">*</span>
            </span>
            <div className="flex-1">
              <Input
                value={draft[key]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
                className={cn("font-mono text-xs", errors[key] && "border-red focus:border-red focus:ring-red/20")}
              />
              {errors[key] && <small className="text-red text-xs">{errors[key]}</small>}
            </div>
          </div>
        ))}
        <div className="flex justify-end gap-2 pt-1">
          <Button variant="outline" size="sm" onClick={onCancel} disabled={saving}>
            <X className="h-3.5 w-3.5" />Cancel
          </Button>
          <Button size="sm" onClick={onSave} disabled={saving}>
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
            Save
          </Button>
        </div>
      </div>
    </section>
  );
}

function initials(name: string): string {
  return name
    .split(" ")
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}