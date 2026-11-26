package localtree

import (
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
)

var cinemaMount = regexp.MustCompile(`^/[\pL\pN][\pL\pN_.-]{0,63}$`)

// Load once per scan so an entire snapshot sees the same set of libraries.
func cinemaScanPrefixes(file string, configured []string) ([]string, error) {
	data, err := os.ReadFile(file)
	if os.IsNotExist(err) {
		return configured, nil
	}
	if err != nil {
		return nil, err
	}
	var inventory struct{ Mounts []string }
	if err := json.Unmarshal(data, &inventory); err != nil {
		return nil, err
	}
	if len(inventory.Mounts) == 0 {
		return nil, fmt.Errorf("empty mount inventory")
	}
	prefixes := append([]string(nil), configured...)
	for _, mount := range inventory.Mounts {
		if !cinemaMount.MatchString(mount) || strings.Contains(strings.ToLower(mount), ".balance") {
			return nil, fmt.Errorf("invalid cinema mount")
		}
		prefixes = append(prefixes, mount+"/Movies", mount+"/TV")
	}
	return prefixes, nil
}
