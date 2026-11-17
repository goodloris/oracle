// Command dbrpc is the evaluator-side fixture/snapshot/fault RPC for the v7 Oracle.
// It reads one JSON request per stdin line and writes one JSON reply per stdout line.
// It refuses any database whose name does not end in _poc. Never given to generation.
package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"os"
	"sort"
	"strings"

	"github.com/jackc/pgx/v5"
)

type request struct {
	Op     string                      `json:"op"`
	Files  []string                    `json:"files"`
	Tables map[string][]map[string]any `json:"tables"`
	Table  *string                     `json:"table"`
}

// Static reference tables seeded by the schema; reset keeps them, snapshot includes them.
var static = map[string]bool{"TenantMember": true, "Project": true}

func main() {
	admin := os.Getenv("ADMIN_DATABASE_URL")
	u, err := url.Parse(admin)
	if os.Getenv("POC_DB_RESET") != "1" || admin == "" || err != nil || !strings.HasSuffix(u.Path, "_poc") {
		fmt.Fprintln(os.Stderr, "disposable _poc database required")
		os.Exit(2)
	}
	ctx := context.Background()
	db, err := pgx.Connect(ctx, admin)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	defer db.Close(ctx)
	in := bufio.NewScanner(os.Stdin)
	in.Buffer(make([]byte, 1<<20), 1<<26)
	out := json.NewEncoder(os.Stdout)
	for in.Scan() {
		var r request
		var reply any
		if err = json.Unmarshal(in.Bytes(), &r); err == nil {
			reply, err = handle(ctx, db, r)
		}
		if err != nil {
			_, _ = db.Exec(ctx, "ROLLBACK")
			_ = out.Encode(map[string]any{"ok": false, "error": err.Error()})
			continue
		}
		_ = out.Encode(map[string]any{"ok": true, "value": reply})
	}
}

func tables(ctx context.Context, db *pgx.Conn) ([]string, error) {
	rows, err := db.Query(ctx, `SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename`)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, pgx.RowTo[string])
}

func clearFault(ctx context.Context, db *pgx.Conn) error {
	names, err := tables(ctx, db)
	if err != nil {
		return err
	}
	for _, t := range names {
		if _, err = db.Exec(ctx, `DROP TRIGGER IF EXISTS v7_fail ON `+pgx.Identifier{t}.Sanitize()); err != nil {
			return err
		}
	}
	_, err = db.Exec(ctx, `DROP FUNCTION IF EXISTS v7_failure()`)
	return err
}

func handle(ctx context.Context, db *pgx.Conn, r request) (any, error) {
	switch r.Op {
	case "init":
		if _, err := db.Exec(ctx, `DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO public`); err != nil {
			return nil, err
		}
		var exists bool
		if err := db.QueryRow(ctx, `SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='app_runtime')`).Scan(&exists); err != nil {
			return nil, err
		}
		files := r.Files
		if exists { // bootstrap.sql creates the role; the schema grant is re-applied below
			files = files[1:]
			if _, err := db.Exec(ctx, `GRANT USAGE ON SCHEMA public TO app_runtime`); err != nil {
				return nil, err
			}
		}
		for _, f := range files {
			sql, err := os.ReadFile(f)
			if err != nil {
				return nil, err
			}
			if _, err = db.Exec(ctx, string(sql)); err != nil {
				return nil, fmt.Errorf("%s: %w", f, err)
			}
		}
		return true, nil
	case "reset":
		if err := clearFault(ctx, db); err != nil {
			return nil, err
		}
		names, err := tables(ctx, db)
		if err != nil {
			return nil, err
		}
		var dynamic []string
		for _, t := range names {
			if !static[t] {
				dynamic = append(dynamic, pgx.Identifier{t}.Sanitize())
			}
		}
		if _, err = db.Exec(ctx, "BEGIN"); err != nil {
			return nil, err
		}
		if _, err = db.Exec(ctx, `TRUNCATE `+strings.Join(dynamic, ",")+` CASCADE`); err != nil {
			return nil, err
		}
		// Purchase first: the other fixture tables reference it.
		order := []string{"Purchase"}
		for t := range r.Tables {
			if t != "Purchase" {
				order = append(order, t)
			}
		}
		sort.Strings(order[1:])
		for _, t := range order {
			for _, row := range r.Tables[t] {
				cols := make([]string, 0, len(row))
				for c := range row {
					cols = append(cols, c)
				}
				sort.Strings(cols)
				quoted, holders, args := []string{}, []string{}, []any{}
				for i, c := range cols {
					quoted = append(quoted, pgx.Identifier{c}.Sanitize())
					holders = append(holders, fmt.Sprintf("$%d", i+1))
					args = append(args, row[c])
				}
				if _, err = db.Exec(ctx, `INSERT INTO `+pgx.Identifier{t}.Sanitize()+` (`+strings.Join(quoted, ",")+`) VALUES (`+strings.Join(holders, ",")+`)`, args...); err != nil {
					return nil, fmt.Errorf("fixture %s: %w", t, err)
				}
			}
		}
		_, err = db.Exec(ctx, "COMMIT")
		return true, err
	case "snapshot":
		names, err := tables(ctx, db)
		if err != nil {
			return nil, err
		}
		result := map[string][]json.RawMessage{}
		for _, t := range names {
			rows, err := db.Query(ctx, `SELECT to_jsonb(t)::text FROM `+pgx.Identifier{t}.Sanitize()+` t`)
			if err != nil {
				return nil, err
			}
			values, err := pgx.CollectRows(rows, pgx.RowTo[string])
			if err != nil {
				return nil, err
			}
			result[t] = []json.RawMessage{}
			for _, v := range values {
				result[t] = append(result[t], json.RawMessage(v))
			}
		}
		return result, nil
	case "env":
		// The database the Oracle runs against: server version and the database's collation and ctype.
		// byte_order: whether the default collation orders the Oracle's "Zed" before "p1" like UTF-8 bytes do.
		var version, collate, ctype, provider string
		var bytes bool
		err := db.QueryRow(ctx, `SELECT version(), datcollate, datctype, datlocprovider::text, 'v7-Zed' < 'v7-p1' FROM pg_database WHERE datname=current_database()`).
			Scan(&version, &collate, &ctype, &provider, &bytes)
		return map[string]any{"version": version, "collate": collate, "ctype": ctype, "provider": provider, "byte_order": bytes}, err
	case "fault":
		if err := clearFault(ctx, db); err != nil {
			return nil, err
		}
		if r.Table == nil {
			return true, nil
		}
		if _, err := db.Exec(ctx, `CREATE FUNCTION v7_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'controlled v7 write failure' USING ERRCODE='P0001'; END $$`); err != nil {
			return nil, err
		}
		_, err := db.Exec(ctx, `CREATE TRIGGER v7_fail BEFORE INSERT ON `+pgx.Identifier{*r.Table}.Sanitize()+` FOR EACH ROW EXECUTE FUNCTION v7_failure()`)
		return true, err
	}
	return nil, errors.New("unknown op")
}
