package migrations

import (
	"github.com/pocketbase/pocketbase/core"
	m "github.com/pocketbase/pocketbase/migrations"
)

func init() {
	m.Register(func(app core.App) error {
		// === UP ===
		return nil
	}, func(app core.App) error {
		// === DOWN ===
		return nil
	})
}
