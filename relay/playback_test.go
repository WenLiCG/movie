package emby

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/AmbitiousJun/go-emby2openlist/v2/internal/config"
	"github.com/AmbitiousJun/go-emby2openlist/v2/internal/service/openlist"
	"github.com/AmbitiousJun/go-emby2openlist/v2/internal/service/openlist/localtree"
	"github.com/AmbitiousJun/go-emby2openlist/v2/internal/util/mp4s"
	"github.com/gin-gonic/gin"
)

func TestStrictVirtualIndexUsesCloudDuration(t *testing.T) {
	old := config.C
	defer func() { config.C = old }()
	for _, test := range []struct {
		meta string
		want time.Duration
	}{
		{`{"duration":5629.6}`, 5629600 * time.Millisecond},
		{`{"duration":0}`, 3 * time.Hour},
		{`{"duration":-1}`, 3 * time.Hour},
		{`{"duration":1e100}`, 3 * time.Hour},
	} {
		t.Run(test.meta, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path != "/api/fs/other" || r.Method != http.MethodPost {
					t.Errorf("unexpected metadata request: %s %s", r.Method, r.URL.Path)
				}
				w.Header().Set("Content-Type", "application/json")
				_, _ = io.WriteString(w, `{"code":200,"data":{"video_preview_play_info":{"meta":`+test.meta+`}}}`)
			}))
			defer server.Close()
			config.C = &config.Config{Openlist: &config.Openlist{Host: server.URL, Token: "fixture", LocalTreeGen: &config.LocalTreeGen{}}}
			target := filepath.Join(t.TempDir(), "movie.mkv")
			if err := (&localtree.VirtualWriter{}).Write(localtree.FileTask{Path: "/aliyun/Movies/Movie.mkv"}, target); err != nil {
				t.Fatal(err)
			}
			body, err := os.ReadFile(target)
			if err != nil || !bytes.Equal(body, mp4s.GenWithDuration(test.want)) {
				t.Fatalf("index duration mismatch; want %v", test.want)
			}
		})
	}
}

func TestStrictPlaybackEvidenceAndRedaction(t *testing.T) {
	old := playbackLogPath
	playbackLogPath = filepath.Join(t.TempDir(), "events.jsonl")
	defer func() { playbackLogPath = old }()
	playbackRecent = map[string]int64{}
	cases := []struct {
		location, content, delivery string
		status                      int
	}{
		{"https://dl1-v6.aliyundrive.cloud/file?sign=private-sign", "text/html", "cloud", 307},
		{"https://aliyundrive.cloud.evil.example/file?token=private-sign", "text/html", "unknown", 307},
		{"", "application/vnd.apple.mpegurl", "playlist", 200},
		{"", "video/mp4", "vps", 206},
		{"", "text/plain", "failed", 403},
	}
	for _, test := range cases {
		_ = os.Remove(playbackLogPath)
		playbackRecent = map[string]int64{}
		router := gin.New()
		router.Use(PlaybackRecorder())
		router.GET("/videos/42/stream", func(c *gin.Context) {
			c.Set("authorizedItemID", "42")
			c.Header("Location", test.location)
			c.Data(test.status, test.content, []byte("payload"))
		})
		request := httptest.NewRequest("GET", "/videos/42/stream?api_key=private-token", nil)
		router.ServeHTTP(httptest.NewRecorder(), request)
		raw, err := os.ReadFile(playbackLogPath)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(raw), "private-") || strings.Contains(string(raw), "https://") {
			t.Fatal("secret in playback log")
		}
		var event playbackEvent
		if err = json.Unmarshal(raw, &event); err != nil {
			t.Fatal(err)
		}
		if event.Delivery != test.delivery || event.Item != "42" {
			t.Fatalf("unexpected evidence: %+v", event)
		}
	}
}

func TestStrictPlaybackPreservesReportAndThrottles(t *testing.T) {
	old := playbackLogPath
	playbackLogPath = filepath.Join(t.TempDir(), "events.jsonl")
	defer func() { playbackLogPath = old }()
	playbackRecent = map[string]int64{}
	body := `{"ItemId":"42","PlaySessionId":"0123456789abcdef0123456789abcdef","PositionTicks":150000000,"IsPaused":true}`
	router := gin.New()
	router.Use(PlaybackRecorder())
	router.POST("/emby/Sessions/Playing/Progress", func(c *gin.Context) {
		raw, _ := io.ReadAll(c.Request.Body)
		if string(raw) != body {
			t.Fatal("playback report changed")
		}
		c.Status(http.StatusNoContent)
	})
	for i := 0; i < 2; i++ {
		r := httptest.NewRequest("POST", "/emby/Sessions/Playing/Progress?X-Emby-Client=Emby%20Web&X-Emby-Device-Name=Chrome%20Windows", strings.NewReader(body))
		r.Header.Set("X-Emby-Token", "private-token")
		router.ServeHTTP(httptest.NewRecorder(), r)
	}
	raw, _ := os.ReadFile(playbackLogPath)
	if strings.Count(string(raw), "\n") != 1 {
		t.Fatal("duplicate progress was not throttled")
	}
	var event playbackEvent
	_ = json.Unmarshal(raw, &event)
	if event.Position != 150000000 || !event.Paused {
		t.Fatal("progress missing")
	}
	if event.Client != "Emby Web" || event.Device != "Chrome Windows" {
		t.Fatal("client query metadata missing")
	}
}

func TestStrictPlaybackObservesFirstVideoBytesBeforeStreamFinishes(t *testing.T) {
	old := playbackLogPath
	playbackLogPath = filepath.Join(t.TempDir(), "events.jsonl")
	defer func() { playbackLogPath = old }()
	playbackRecent = map[string]int64{}
	router := gin.New()
	router.Use(PlaybackRecorder())
	router.GET("/videos/42/stream", func(c *gin.Context) {
		c.Set("authorizedItemID", "42")
		c.Header("Content-Type", "video/mp4")
		_, _ = c.Writer.Write([]byte("first bytes"))
		raw, err := os.ReadFile(playbackLogPath)
		if err != nil {
			t.Fatal("video route not observed until stream ended")
		}
		var event playbackEvent
		_ = json.Unmarshal(raw, &event)
		if event.Delivery != "vps" {
			t.Fatal("video route not classified")
		}
		_, _ = c.Writer.Write([]byte("next bytes"))
	})
	r := httptest.NewRequest("GET", "/videos/42/stream?api_key=private-token", nil)
	router.ServeHTTP(httptest.NewRecorder(), r)
	raw, _ := os.ReadFile(playbackLogPath)
	if strings.Count(string(raw), "\n") != 1 {
		t.Fatal("recorded every video chunk")
	}
}

func TestStrictChineseCloudSourceSurvivesFormEncoding(t *testing.T) {
	old := config.C
	defer func() { config.C = old }()
	cloudPath := "/aliyun/Movies/空枪.V.2026.1080p.WEB-DL.H.264.AAC2.0-HHWEB/空枪.V.2026.1080p.WEB-DL.H.264.AAC2.0-HHWEB.mp4"
	encoded := openlist.PathEncode(cloudPath)
	if !strings.Contains(encoded, "+") {
		t.Fatal("regression fixture must contain a base64 plus")
	}
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"MediaSources": []map[string]string{{"Id": "mediasource_42", "Path": "/library" + cloudPath}}})
	}))
	defer origin.Close()
	config.C = &config.Config{Emby: &config.Emby{Host: origin.URL, MountPath: "/library"}, Path: &config.Path{}}
	_ = config.C.Path.Init()
	router := gin.New()
	router.Use(ApiKeyChecker())
	router.GET("/videos/:id/stream", Redirect2OpenlistLink)
	request := func(path string) int {
		encoded = strings.ReplaceAll(openlist.PathEncode(path), "+", " ")
		q := url.Values{"MediaSourceId": {"mediasource_42[[_]]FHD[[_]]1920x1080[[_]]" + encoded}, "api_key": {"session"}}
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest("GET", "/videos/42/stream?"+q.Encode(), nil))
		return w.Code
	}
	if got := request(cloudPath); got != 307 {
		t.Fatalf("Chinese FHD source rejected: %d", got)
	}
	if got := request("/aliyun/Movies/Other.mp4"); got != 403 {
		t.Fatal("different movie path was authorized")
	}
	raw := "/aliyun/a+b c.mp4"
	if openlist.PathDecode(raw) != raw {
		t.Fatal("raw path changed")
	}
}

func TestStrictCloudPreviewKeepsHLSWithCacheDisabled(t *testing.T) {
	old := config.C
	defer func() { config.C = old }()
	origin := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"MediaSources": []map[string]any{{"Id": "mediasource_42", "ItemId": "42", "Path": "/library/ali/Movie.mp4", "Container": "mp4", "Name": "Fixture", "MediaStreams": []any{}}}})
	}))
	defer origin.Close()
	config.C = &config.Config{Emby: &config.Emby{Host: origin.URL, MountPath: "/library"}, Path: &config.Path{}, Cache: &config.Cache{Enable: false}}
	_ = config.C.Path.Init()
	router := gin.New()
	router.POST("/Items/:id/PlaybackInfo", TransferPlaybackInfo)
	request := func(source string) map[string]any {
		q := url.Values{"MediaSourceId": {source}, "X-Emby-Token": {"session"}}
		w := httptest.NewRecorder()
		router.ServeHTTP(w, httptest.NewRequest("POST", "/Items/42/PlaybackInfo?"+q.Encode(), strings.NewReader("{}")))
		if w.Code != 200 {
			t.Fatalf("playback info failed: %d", w.Code)
		}
		var result struct{ MediaSources []map[string]any }
		if err := json.Unmarshal(w.Body.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		return result.MediaSources[0]
	}
	source := request("mediasource_42[[_]]FHD[[_]]1920x1080[[_]]" + openlist.PathEncode("/ali/Movie.mp4"))
	if source["SupportsTranscoding"] != true || source["TranscodingSubProtocol"] != "hls" || source["SupportsDirectStream"] != false {
		t.Fatal("cloud HLS was incorrectly advertised as a direct MP4")
	}
	if _, exists := source["DirectStreamUrl"]; exists {
		t.Fatal("cloud HLS still has a progressive stream URL")
	}
	u, err := url.Parse(source["TranscodingUrl"].(string))
	if err != nil {
		t.Fatal(err)
	}
	if u.Query().Get("api_key") != "session" || u.Query().Get("template_id") != "FHD" {
		t.Fatal("HLS authentication or template lost")
	}
	original := request("mediasource_42")
	if original["SupportsDirectStream"] != true || original["SupportsTranscoding"] != false {
		t.Fatal("original playback policy changed")
	}
}
