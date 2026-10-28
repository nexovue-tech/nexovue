package config

import (
	"net"
	"net/url"
	"strings"
)

const clickHouseDSNVariable = "CLICKHOUSE_DSN"

// Workers speak ClickHouse's native protocol, so the DSN names the native port,
// not the HTTP one the API uses. Both services read CLICKHOUSE_DSN; the value
// differs per service and `.env.example` says so.
const (
	clickHouseScheme       = "clickhouse"
	clickHouseSecureScheme = "clickhouses"
	defaultNativePort      = "9000"
	secureQueryParameter   = "secure"
)

// ClickHouseTarget is a validated ClickHouse connection, in the shape the
// native client takes.
type ClickHouseTarget struct {
	Address  string
	Database string
	User     string
	Password string
	Secure   bool
}

// ParseClickHouseDSN reads CLICKHOUSE_DSN's value the way Load does, for a command that needs
// only the ClickHouse connection (cmd/setup).
func ParseClickHouseDSN(dsn string) (ClickHouseTarget, error) {
	return parseClickHouseDSN(clickHouseDSNVariable, dsn)
}

// parseClickHouseDSN turns clickhouse://user:password@host:9000/database into a
// connection target, naming the environment variable on any failure.
func parseClickHouseDSN(variable, dsn string) (ClickHouseTarget, error) {
	parsed, err := url.Parse(dsn)
	if err != nil {
		return ClickHouseTarget{}, invalid(variable, "is not a URL: %v", err)
	}
	if parsed.Scheme != clickHouseScheme && parsed.Scheme != clickHouseSecureScheme {
		return ClickHouseTarget{}, invalid(variable,
			"got scheme %q, want %s:// or %s://", parsed.Scheme,
			clickHouseScheme, clickHouseSecureScheme)
	}
	if parsed.Hostname() == "" {
		return ClickHouseTarget{}, invalid(variable, "has no host")
	}

	database := strings.TrimPrefix(parsed.Path, "/")
	if database == "" {
		return ClickHouseTarget{}, invalid(variable, "has no database in its path")
	}

	port := parsed.Port()
	if port == "" {
		port = defaultNativePort
	}

	password, _ := parsed.User.Password()
	return ClickHouseTarget{
		Address:  net.JoinHostPort(parsed.Hostname(), port),
		Database: database,
		User:     parsed.User.Username(),
		Password: password,
		Secure: parsed.Scheme == clickHouseSecureScheme ||
			parsed.Query().Get(secureQueryParameter) == "true",
	}, nil
}
