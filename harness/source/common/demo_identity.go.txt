package app

import "strings"

// AuthenticateDemo exposes the existing fixed POC identities to additive demos.
// Production identity verification remains outside this POC.
func AuthenticateDemo(header string) (Identity, bool) {
	if !strings.HasPrefix(header, "Bearer ") {
		return Identity{}, false
	}
	u, ok := identities[strings.TrimPrefix(header, "Bearer ")]
	return u, ok
}
