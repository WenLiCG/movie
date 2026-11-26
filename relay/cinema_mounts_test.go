package localtree

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/AmbitiousJun/go-emby2openlist/v2/internal/config"
)

func TestStrictCinemaScanBoundaries(t *testing.T) {
	file := filepath.Join(t.TempDir(), "mounts")
	cfg := config.LocalTreeGen{ScanPrefixes: []string{"/aliyun/Movies"}}
	for _, contents := range []string{"", `{"mounts":["/电影库"]}`, `{"mounts":["/other/nested"]}`} {
		if contents != "" {
			if err := os.WriteFile(file, []byte(contents), 0600); err != nil {
				t.Fatal(err)
			}
		}
		prefixes, err := cinemaScanPrefixes(file, cfg.ScanPrefixes)
		if contents == `{"mounts":["/other/nested"]}` {
			if err == nil {
				t.Fatal("accepted nested mount")
			}
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		scan := config.LocalTreeGen{ScanPrefixes: prefixes}
		if !scan.IsValidPrefix("/aliyun/Movies/file") || scan.IsValidPrefix("/aliyun/Movies-old") ||
			scan.IsValidPrefix("/电影库/.cinema-upload") || scan.IsValidPrefix("/电影") ||
			scan.IsValidPrefix("/电影库/TV/episode") != (contents != "") {
			t.Fatal("wrong scan scope", prefixes)
		}
	}
}
