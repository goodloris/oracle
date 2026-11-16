package app

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Identity struct{ User, Tenant, Role string }

var identities = map[string]Identity{
	"demo_alice": {"alice", "tenant-a", "requester"},
	"demo_bob":   {"bob", "tenant-a", "approver"},
	"demo_carol": {"carol", "tenant-a", "approver"},
	"demo_dave":  {"dave", "tenant-b", "requester"},
	"demo_erin":  {"erin", "tenant-b", "approver"},
}

type Purchase struct {
	ID           string  `json:"id"`
	Tenant       string  `json:"tenantId"`
	Creator      string  `json:"createdBy"`
	Title        string  `json:"title"`
	Amount       int     `json:"amount"`
	Status       string  `json:"status"`
	InternalNote *string `json:"internalNote,omitempty"`
}
type Server struct {
	Pool     *pgxpool.Pool
	Guarded  bool
	Mutation string
}

const projection = `id,"tenantId","createdBy",title,amount,status`

func New(ctx context.Context, url, mode, mutation string) (*Server, error) {
	if mode != "baseline" && mode != "guarded" {
		return nil, fmt.Errorf("invalid mode")
	}
	if mutation != "" && os.Getenv("POC_ALLOW_MUTATIONS") != "1" {
		return nil, fmt.Errorf("fault injection requires POC_ALLOW_MUTATIONS=1")
	}
	if mutation != "" && mutation != "omit-tenant" && mutation != "skip-approval-check" && mutation != "leak-field" && mutation != "mass-assignment" && mutation != "bypass-action" {
		return nil, fmt.Errorf("unknown mutation")
	}
	pool, e := pgxpool.New(ctx, url)
	if e != nil {
		return nil, e
	}
	var unsafe bool
	e = pool.QueryRow(ctx, `SELECT rolsuper OR rolbypassrls OR pg_has_role(current_user, c.relowner, 'MEMBER') FROM pg_roles r CROSS JOIN pg_class c WHERE r.rolname=current_user AND c.oid='"Purchase"'::regclass`).Scan(&unsafe)
	if e != nil || unsafe {
		pool.Close()
		return nil, fmt.Errorf("runtime must use a non-owner, non-superuser, non-bypassrls role: %v", e)
	}
	if mode == "guarded" {
		var configured bool
		e = pool.QueryRow(ctx, `SELECT relrowsecurity AND relforcerowsecurity
          AND NOT has_column_privilege(current_user,c.oid,'internalNote','SELECT')
          AND NOT has_column_privilege(current_user,c.oid,'tenantId','UPDATE')
          AND NOT has_column_privilege(current_user,c.oid,'createdBy','UPDATE')
          AND (SELECT count(*)=3 FROM pg_policy p WHERE p.polrelid=c.oid)
          FROM pg_class c WHERE c.oid='"Purchase"'::regclass`).Scan(&configured)
		if e != nil || !configured {
			pool.Close()
			return nil, fmt.Errorf("guarded runtime requires configured RLS and column privileges: %v", e)
		}
	}
	return &Server{pool, mode == "guarded", mutation}, nil
}
func write(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
func deny(w http.ResponseWriter, status int) {
	write(w, status, map[string]string{"error": http.StatusText(status)})
}
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/health" {
		if e := s.Pool.Ping(r.Context()); e != nil {
			deny(w, 503)
		} else {
			write(w, 200, map[string]bool{"ok": true})
		}
		return
	}
	user, ok := identities[strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")]
	if !ok || !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") {
		deny(w, 401)
		return
	}
	if r.URL.Path != "/purchases" && !strings.HasPrefix(r.URL.Path, "/purchases/") {
		deny(w, 404)
		return
	}
	tx, e := s.Pool.Begin(r.Context())
	if e != nil {
		s.fail(w, e)
		return
	}
	defer tx.Rollback(r.Context())
	if s.Guarded {
		_, e = tx.Exec(r.Context(), `SELECT set_config('app.tenant',$1,true),set_config('app.user',$2,true),set_config('app.role',$3,true)`, user.Tenant, user.User, user.Role)
		if e != nil {
			s.fail(w, e)
			return
		}
	}
	result, status, e := s.handle(r, tx, user)
	if e != nil {
		s.fail(w, e)
		return
	}
	if status >= 400 {
		deny(w, status)
		return
	}
	if e = tx.Commit(r.Context()); e != nil {
		s.fail(w, e)
		return
	}
	write(w, status, result)
}
func (s *Server) fail(w http.ResponseWriter, e error) {
	var pg *pgconn.PgError
	if errors.As(e, &pg) {
		switch pg.Code {
		case "42501":
			deny(w, 403)
			return
		case "23514", "23502", "22003", "22P02":
			deny(w, 400)
			return
		case "23505":
			deny(w, 409)
			return
		}
	}
	if errors.Is(e, pgx.ErrNoRows) {
		deny(w, 404)
		return
	}
	log.Printf("request failed: %v", e)
	deny(w, 500)
}
func (s *Server) handle(r *http.Request, tx pgx.Tx, u Identity) (any, int, error) {
	ctx := r.Context()
	parts := strings.Split(strings.Trim(r.URL.Path, "/"), "/")
	if r.Method == "GET" && (len(parts) == 1 || len(parts) == 2) {
		cols := projection
		if s.Mutation == "leak-field" {
			cols += `,"internalNote"`
		}
		query := `SELECT ` + cols + ` FROM "Purchase" WHERE true`
		args := []any{}
		if !s.Guarded && s.Mutation != "omit-tenant" {
			args = append(args, u.Tenant)
			query += fmt.Sprintf(` AND "tenantId"=$%d`, len(args))
		}
		if len(parts) == 2 {
			args = append(args, parts[1])
			query += fmt.Sprintf(` AND id=$%d`, len(args))
		}
		query += ` ORDER BY id`
		rows, e := tx.Query(ctx, query, args...)
		if e != nil {
			return nil, 0, e
		}
		defer rows.Close()
		result := []Purchase{}
		for rows.Next() {
			var p Purchase
			dest := []any{&p.ID, &p.Tenant, &p.Creator, &p.Title, &p.Amount, &p.Status}
			if s.Mutation == "leak-field" {
				dest = append(dest, &p.InternalNote)
			}
			if e = rows.Scan(dest...); e != nil {
				return nil, 0, e
			}
			result = append(result, p)
		}
		if e = rows.Err(); e != nil {
			return nil, 0, e
		}
		if len(parts) == 2 {
			if len(result) == 0 {
				return nil, 404, nil
			}
			return result[0], 200, nil
		}
		return result, 200, nil
	}
	if r.Method == "POST" && len(parts) == 1 {
		b, e := io.ReadAll(http.MaxBytesReader(nil, r.Body, 65536))
		if e != nil {
			return nil, 400, nil
		}
		var data map[string]json.RawMessage
		if json.Unmarshal(b, &data) != nil || data == nil {
			return nil, 400, nil
		}
		if s.Mutation != "mass-assignment" {
			for k := range data {
				if k != "id" && k != "title" && k != "amount" {
					return nil, 400, nil
				}
			}
		}
		p := Purchase{Tenant: u.Tenant, Creator: u.User, Status: "submitted"}
		if json.Unmarshal(b, &p) != nil {
			return nil, 400, nil
		}
		if s.Mutation != "mass-assignment" {
			p.Tenant = u.Tenant
			p.Creator = u.User
			p.Status = "submitted"
		}
		if p.ID == "" || len(p.ID) > 100 || len(p.Title) == 0 || len(p.Title) > 200 || p.Amount <= 0 || p.Amount > 2147483647 {
			return nil, 400, nil
		}
		if !s.Guarded && u.Role != "requester" {
			return nil, 403, nil
		}
		_, e = tx.Exec(ctx, `INSERT INTO "Purchase" (id,"tenantId","createdBy",title,amount,status) VALUES ($1,$2,$3,$4,$5,$6)`, p.ID, p.Tenant, p.Creator, p.Title, p.Amount, p.Status)
		if e != nil {
			return nil, 0, e
		}
		p.InternalNote = nil
		return p, 201, nil
	}
	approve := r.Method == "POST" && len(parts) == 3 && parts[2] == "approve"
	bypass := r.Method == "PATCH" && len(parts) == 2 && s.Mutation == "bypass-action"
	if approve || bypass {
		if !s.Guarded && !bypass && s.Mutation != "skip-approval-check" && u.Role != "approver" {
			return nil, 403, nil
		}
		query := `UPDATE "Purchase" SET status='approved' WHERE id=$1`
		args := []any{parts[1]}
		if !s.Guarded && !bypass {
			query += ` AND "tenantId"=$2 AND status='submitted'`
			args = append(args, u.Tenant)
			if s.Mutation != "skip-approval-check" {
				query += ` AND "createdBy"<>$3`
				args = append(args, u.User)
			}
		}
		query += ` RETURNING ` + projection
		var p Purchase
		e := tx.QueryRow(ctx, query, args...).Scan(&p.ID, &p.Tenant, &p.Creator, &p.Title, &p.Amount, &p.Status)
		return p, 200, e
	}
	if r.Method == "PATCH" {
		return nil, 403, nil
	}
	return nil, 404, nil
}
