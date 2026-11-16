// Package opdef is the reviewed, protected catalog of business operation
// semantics. The kernel (internal/opkernel) executes an operation only by
// looking up its catalog entry by name, and the Verifier (internal/opcheck)
// checks extension code against the same entries. Extension code may only
// refer to entries (the Name constants below, or Lookup); it never builds an
// Operation, and cannot choose an event or an event exemption.
//
// Adding a business operation means adding an entry to catalog.go, which is a
// protected path that needs owner review; only then does an agent write the
// adapter code that calls the kernel with the new Name.
//
// This is a small typed catalog, not a DSL. Event is required by default: an
// entry without an event must carry its own EventExemption with a reason.
package opdef

import (
	"crypto/sha256"
	"embed"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"sort"
)

// Name identifies one catalog entry, versioned like "bulk-reassign.v6".
type Name string

// Kind is the semantic class of an operation.
type Kind string

const (
	Transition  Kind = "transition"   // moves Purchase.status From -> To
	FieldUpdate Kind = "field-update" // sets one managed column, keeps status
)

// Subject says whose purchases the actor may target.
type Subject string

const (
	Own    Subject = "own"    // purchases the actor created
	Others Subject = "others" // purchases created by someone else
)

// Record is the append-only table that records every target of a call.
type Record string

const (
	ApprovalAudit     Record = "ApprovalAudit"     // status transitions (from, to, reason)
	AssignmentHistory Record = "AssignmentHistory" // column changes (from, to value)
)

// Separation is a segregation-of-duties requirement beyond Subject.
type Separation string

const (
	NoSeparation Separation = ""
	// DistinctFromPreviousApprover: the actor differs from the approver whose
	// audit row moved the purchase into From.
	DistinctFromPreviousApprover Separation = "distinct-from-previous-approver"
)

// UniformDenial is the one status every ineligible target gets (missing,
// other tenant, wrong owner, wrong state, failed separation or confirmation),
// so an Action never reveals why or whether a target exists.
const UniformDenial = 409

// Request field names used in Inputs and Fingerprint. A field-update entry's
// value field is named by its Param.
const (
	IDs    = "ids"    // the targets (one for a single-target entry)
	Reason = "reason" // optional free text, recorded in ApprovalAudit, never hashed
	Amount = "amount" // amount the actor confirms (ConfirmAmount)
)

// Tier routes batch targets whose amount is above Over to To instead.
type Tier struct {
	Over int    `json:"over"`
	To   string `json:"to"`
}

// Operation is one versioned catalog entry.
type Operation struct {
	Name    Name    `json:"name"`
	Kind    Kind    `json:"kind"`
	Role    string  `json:"role"`
	Subject Subject `json:"subject"`
	// Transition: From -> To, optionally tiered.
	From string `json:"from,omitempty"`
	To   string `json:"to,omitempty"`
	Tier *Tier  `json:"tier,omitempty"`
	// FieldUpdate: targets must have Status and keep it; Column takes the
	// request value named Param.
	Status string `json:"status,omitempty"`
	Column string `json:"column,omitempty"`
	Param  string `json:"param,omitempty"`
	// Response is the array field of a batch response; "" means a single
	// target whose response is {"id":...,"status":To}.
	Response string `json:"response,omitempty"`
	// Inputs lists the request fields in request order (IDs first). This is
	// also the order of the protocol handler parameters after the key.
	Inputs []string `json:"inputs"`
	// Fingerprint lists the Inputs bound into requestHash, in digest order.
	// Inputs not listed, and the Idempotency-Key, are never hashed.
	Fingerprint []string `json:"fingerprint"`
	Record      Record   `json:"record"`
	// Event is the PurchaseEvent type written per target in the same
	// transaction. Exactly one of Event and EventExemption is set.
	Event          string     `json:"event,omitempty"`
	EventExemption string     `json:"eventExemption,omitempty"`
	Separation     Separation `json:"separation,omitempty"`
	ConfirmAmount  bool       `json:"confirmAmount,omitempty"`
	Denial         int        `json:"denial"`
}

// Single reports whether the entry takes exactly one target.
func (o Operation) Single() bool { return o.Response == "" }

func (o Operation) clone() Operation {
	o.Inputs = slices.Clone(o.Inputs)
	o.Fingerprint = slices.Clone(o.Fingerprint)
	if o.Tier != nil {
		t := *o.Tier
		o.Tier = &t
	}
	return o
}

var index = func() map[Name]Operation {
	m := map[Name]Operation{}
	for _, o := range catalog {
		m[o.Name] = o
	}
	return m
}()

// Lookup returns a copy of the entry; callers cannot change the catalog.
func Lookup(name Name) (Operation, bool) {
	o, ok := index[name]
	if !ok {
		return Operation{}, false
	}
	return o.clone(), true
}

// All returns copies of every entry sorted by name.
func All() []Operation {
	out := make([]Operation, 0, len(catalog))
	for _, o := range catalog {
		out = append(out, o.clone())
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Name < out[j].Name })
	return out
}

// Digest is the content hash of the catalog (canonical JSON of All). The
// Verifier reports it, and catalog_test pins it so any change is visible.
func Digest() string {
	b, err := json.Marshal(All())
	if err != nil {
		panic(err)
	}
	sum := sha256.Sum256(b)
	return "sha256:" + hex.EncodeToString(sum[:])
}

//go:embed catalog.go opdef.go
var sources embed.FS

// SourceFiles are the catalog package sources this binary was built from,
// keyed by file name, so the Verifier can require a checkout's
// internal/opdef to be byte-identical to the catalog it checks against.
func SourceFiles() map[string][]byte {
	out := map[string][]byte{}
	for _, n := range []string{"catalog.go", "opdef.go"} {
		b, err := sources.ReadFile(n)
		if err != nil {
			panic(err)
		}
		out[n] = b
	}
	return out
}

var (
	versioned = regexp.MustCompile(`^[a-z][a-z0-9-]*\.v[0-9]+$`)
	eventType = regexp.MustCompile(`^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$`)
)

// Validate checks every entry for completeness and internal consistency.
func Validate() error {
	seen := map[Name]bool{}
	events := map[string]Name{}
	for _, o := range catalog {
		if err := o.validate(); err != nil {
			return fmt.Errorf("%s: %w", o.Name, err)
		}
		if seen[o.Name] {
			return fmt.Errorf("%s: duplicate name", o.Name)
		}
		seen[o.Name] = true
		if o.Event != "" {
			if prev, dup := events[o.Event]; dup {
				return fmt.Errorf("%s: event %s already used by %s", o.Name, o.Event, prev)
			}
			events[o.Event] = o.Name
		}
	}
	return nil
}

func (o Operation) validate() error {
	switch {
	case !versioned.MatchString(string(o.Name)):
		return fmt.Errorf("name needs a version suffix like .v1")
	case o.Role != "approver" && o.Role != "requester":
		return fmt.Errorf("unknown role %q", o.Role)
	case o.Subject != Own && o.Subject != Others:
		return fmt.Errorf("unknown subject %q", o.Subject)
	case (o.Event == "") == (o.EventExemption == ""):
		return fmt.Errorf("exactly one of Event and EventExemption is required")
	case o.Event != "" && !eventType.MatchString(o.Event):
		return fmt.Errorf("event %q is not <aggregate>.<event>", o.Event)
	case o.Denial != UniformDenial:
		return fmt.Errorf("denial must be the uniform %d", UniformDenial)
	case o.Separation != NoSeparation && (o.Separation != DistinctFromPreviousApprover || o.Subject != Others):
		return fmt.Errorf("separation %q needs subject others", o.Separation)
	}
	switch o.Kind {
	case Transition:
		if o.From == "" || o.To == "" || o.From == o.To || o.Status != "" || o.Column != "" || o.Param != "" || o.Record != ApprovalAudit {
			return fmt.Errorf("transition needs From != To, no column, and ApprovalAudit")
		}
		if o.Tier != nil && (o.Tier.Over < 1 || o.Tier.To == "" || o.Single()) {
			return fmt.Errorf("tier needs Over > 0, To, and a batch response")
		}
	case FieldUpdate:
		if o.Status == "" || o.Column == "" || o.Param == "" || o.Single() || o.From != "" || o.To != "" || o.Tier != nil || o.Record != AssignmentHistory {
			return fmt.Errorf("field update needs Status, Column, Param, a batch response and AssignmentHistory")
		}
	default:
		return fmt.Errorf("unknown kind %q", o.Kind)
	}
	if len(o.Inputs) == 0 || o.Inputs[0] != IDs || len(o.Fingerprint) == 0 || o.Fingerprint[0] != IDs {
		return fmt.Errorf("inputs and fingerprint start with %q", IDs)
	}
	allowed := map[string]bool{IDs: true, Reason: o.Kind == Transition, Amount: o.ConfirmAmount, o.Param: o.Param != ""}
	for i, in := range o.Inputs {
		if !allowed[in] || slices.Index(o.Inputs, in) != i {
			return fmt.Errorf("input %q is unknown or repeated", in)
		}
	}
	if o.ConfirmAmount != slices.Contains(o.Inputs, Amount) || (o.ConfirmAmount && !o.Single()) {
		return fmt.Errorf("amount input is required exactly for a single-target ConfirmAmount entry")
	}
	if o.Param != "" && !slices.Contains(o.Inputs, o.Param) {
		return fmt.Errorf("param %q must be an input", o.Param)
	}
	for i, f := range o.Fingerprint {
		if !slices.Contains(o.Inputs, f) || slices.Index(o.Fingerprint, f) != i || f == Reason {
			return fmt.Errorf("fingerprint field %q must be a distinct non-reason input", f)
		}
	}
	return nil
}
