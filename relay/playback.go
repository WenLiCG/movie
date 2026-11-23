package emby

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"github.com/gin-gonic/gin"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

type playbackEvent struct {
	At       int64  `json:"at"`
	Actor    string `json:"actor"`
	Item     string `json:"item"`
	Session  string `json:"session,omitempty"`
	Kind     string `json:"kind"`
	Client   string `json:"client,omitempty"`
	Device   string `json:"device,omitempty"`
	Position int64  `json:"position,omitempty"`
	Paused   bool   `json:"paused,omitempty"`
	Status   int    `json:"status"`
	Delivery string `json:"delivery,omitempty"`
	Host     string `json:"host,omitempty"`
	Bytes    int    `json:"bytes,omitempty"`
	sample   bool
}

var playbackLogPath = "/app/lib/playback/events.jsonl"
var playbackLogMu sync.Mutex
var playbackRecent = map[string]int64{}
var playbackHeader = regexp.MustCompile(`(?i)\b(Client|Device)="([^"]*)"`)
var playbackSession = regexp.MustCompile(`^[a-fA-F0-9-]{16,64}$`)

type playbackResponseWriter struct {
	gin.ResponseWriter
	once   sync.Once
	record func()
}

func (w *playbackResponseWriter) Write(data []byte) (int, error) {
	n, err := w.ResponseWriter.Write(data)
	if n > 0 {
		w.once.Do(w.record)
	}
	return n, err
}

func (w *playbackResponseWriter) WriteString(data string) (int, error) {
	n, err := w.ResponseWriter.WriteString(data)
	if n > 0 {
		w.once.Do(w.record)
	}
	return n, err
}

// PlaybackRecorder saves response evidence, never tokens, request URLs or signed links.
func PlaybackRecorder() gin.HandlerFunc {
	return func(c *gin.Context) {
		path := strings.ToLower(c.Request.URL.Path)
		sessionPath := strings.TrimPrefix(path, "/emby")
		media := !IsWebAssetPath(path) && strings.Contains(path, "/videos/") && !strings.Contains(path, "subtitle")
		kind := map[string]string{"/sessions/playing": "start", "/sessions/playing/progress": "progress", "/sessions/playing/stopped": "stop"}[sessionPath]
		if !media && (kind == "" || c.Request.Method != http.MethodPost) {
			c.Next()
			return
		}
		_, _, token := getApiKey(c)
		if token == "" || strings.ContainsAny(token, " \"\r\n") {
			c.Next()
			return
		}
		hash := sha256.Sum256([]byte(token))
		e := playbackEvent{At: time.Now().Unix(), Actor: fmt.Sprintf("%x", hash[:12]), Kind: kind}
		e.Client = c.GetHeader("X-Emby-Client")
		if e.Client == "" {
			e.Client = c.Query("X-Emby-Client")
		}
		e.Device = c.GetHeader("X-Emby-Device-Name")
		if e.Device == "" {
			e.Device = c.Query("X-Emby-Device-Name")
		}
		for _, header := range []string{HeaderFullAuthName, HeaderAuthName} {
			for _, match := range playbackHeader.FindAllStringSubmatch(c.GetHeader(header), -1) {
				value := match[2]
				if decoded, err := url.QueryUnescape(value); err == nil {
					value = decoded
				}
				value = strings.ReplaceAll(value, token, "<private>")
				if len(value) > 160 || strings.Contains(value, "://") {
					continue
				}
				if strings.EqualFold(match[1], "Client") {
					e.Client = value
				} else {
					e.Device = value
				}
			}
		}
		e.Client = strings.ReplaceAll(e.Client, token, "<private>")
		e.Device = strings.ReplaceAll(e.Device, token, "<private>")
		if len(e.Client) > 160 || strings.Contains(e.Client, "://") {
			e.Client = ""
		}
		if len(e.Device) > 160 || strings.Contains(e.Device, "://") {
			e.Device = ""
		}
		if kind != "" && c.Request.Body != nil {
			body := c.Request.Body
			raw, err := io.ReadAll(io.LimitReader(body, 65537))
			c.Request.Body = struct {
				io.Reader
				io.Closer
			}{io.MultiReader(bytes.NewReader(raw), body), body}
			var report struct {
				ItemID   string `json:"ItemId"`
				Session  string `json:"PlaySessionId"`
				Position int64  `json:"PositionTicks"`
				Paused   bool   `json:"IsPaused"`
			}
			if err == nil && len(raw) <= 65536 && json.Unmarshal(raw, &report) == nil {
				e.Item = report.ItemID
				e.Session = report.Session
				e.Position = report.Position
				e.Paused = report.Paused
			}
		}
		record := func() {
			e.Status = c.Writer.Status()
			// The recorder runs before ApiKeyChecker. Only keep evidence that passed authorization:
			// media needs the per-item grant, and session reports need Emby to accept them.
			// Otherwise any random token could flood the log and rotate real evidence away.
			if media && c.GetString("authorizedItemID") == "" {
				return
			}
			if !media && (e.Status < 200 || e.Status >= 300) {
				return
			}
			if media {
				e.sample = strings.Contains(path, "/proxy_ts")
				e.Kind = "media"
				e.Item = c.GetString("authorizedItemID")
				if e.Item == "" {
					e.Item = c.Query("item_id")
					if match := playbackItemRoute.FindStringSubmatch(c.Request.URL.Path); e.Item == "" && len(match) > 1 {
						e.Item = match[1]
					}
				}
				e.Session = c.Query("PlaySessionId")
				contentType := strings.ToLower(c.Writer.Header().Get("Content-Type"))
				location, err := url.Parse(c.Writer.Header().Get("Location"))
				switch {
				case e.Status >= 400:
					e.Delivery = "failed"
				case e.Status >= 300 && e.Status < 400 && err == nil && location.IsAbs():
					e.Host = strings.ToLower(location.Hostname())
					e.Delivery = "unknown"
					for _, suffix := range []string{"aliyundrive.cloud", "aliyuncs.com", "alipan.com", "aliyundrive.net"} {
						if location.Scheme == "https" && (e.Host == suffix || strings.HasSuffix(e.Host, "."+suffix)) {
							e.Delivery = "cloud"
						}
					}
				case strings.Contains(contentType, "mpegurl"):
					e.Delivery = "playlist"
				case e.Status >= 200 && e.Status < 300 && c.Writer.Size() > 0 && blocksOriginResponse(c.Writer.Header()):
					e.Delivery = "vps"
					e.Bytes = c.Writer.Size()
				default:
					e.Delivery = "unknown"
				}
			}
			if !validItemID.MatchString(e.Item) || len(e.Item) > 128 {
				return
			}
			if !playbackSession.MatchString(e.Session) {
				e.Session = ""
			}
			if e.Position < 0 {
				e.Position = 0
			}
			writePlaybackEvent(e)
		}
		if media {
			writer := &playbackResponseWriter{ResponseWriter: c.Writer, record: record}
			c.Writer = writer
			c.Next()
			writer.once.Do(record)
		} else {
			c.Next()
			record()
		}
	}
}

func writePlaybackEvent(e playbackEvent) {
	playbackLogMu.Lock()
	defer playbackLogMu.Unlock()
	key := e.Actor + ":" + e.Item + ":" + e.Session + ":" + e.Kind + ":" + e.Delivery + ":" + e.Host + fmt.Sprint(e.Status, e.Paused)
	if e.sample || e.Kind == "progress" {
		if last, ok := playbackRecent[key]; ok && e.At-last < 15 {
			return
		}
	}
	if len(playbackRecent) > 4096 {
		clear(playbackRecent)
	}
	if err := os.MkdirAll(filepath.Dir(playbackLogPath), 0750); err != nil {
		return
	}
	if info, err := os.Stat(playbackLogPath); err == nil && info.Size() >= 2*1024*1024 {
		_ = os.Remove(playbackLogPath + ".1")
		if os.Rename(playbackLogPath, playbackLogPath+".1") != nil {
			return
		}
	}
	file, err := os.OpenFile(playbackLogPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0600)
	if err != nil {
		return
	}
	defer file.Close()
	if json.NewEncoder(file).Encode(e) == nil {
		playbackRecent[key] = e.At
	}
}
