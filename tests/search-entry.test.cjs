// Run: node tests/search-entry.test.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../relay/custom-js/search.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '../relay/custom-css/search.css'), 'utf8');
let header, link, observer, loggedIn = false, videos = [];
const events = new Map();
const addEventListener = (name, callback) => events.set(name, callback);
const newHeader = () => ({
  children: [],
  prepend(node) {
    node.remove();
    this.children.unshift(node);
    node.parentElement = this;
  },
});
header = newHeader();
const document = {
  body: {},
  fullscreenElement: null,
  addEventListener,
  querySelector: () => header,
  querySelectorAll: () => videos,
  createElement(tag) {
    assert.equal(tag, 'a');
    return link = {
      attrs: {},
      parentElement: null,
      setAttribute(key, value) { this.attrs[key] = value; },
      remove() {
        if (this.parentElement) this.parentElement.children = this.parentElement.children.filter(node => node !== this);
        this.parentElement = null;
      },
    };
  },
};
const context = vm.createContext({
  window: { addEventListener }, document,
  location: { hash: '#!/home', hostname: 'movie.example.com', protocol: 'https:' },
  ApiClient: {
    getCurrentUserId: () => loggedIn ? 'user' : null,
    accessToken: () => loggedIn ? 'private-session-token' : null,
  },
  MutationObserver: class {
    constructor(callback) { observer = callback; }
    observe() {}
  },
});
vm.runInContext(script, context);
assert.equal(link.hidden, true, 'signed-out visitors see no entry');
loggedIn = true;
events.get('viewshow')();
assert.equal(link.hidden, false);
assert.equal(link.href, '/discover/', 'navigation carries no token or user ID');
assert.equal(link.target, '_blank');
assert.equal(link.rel, 'noopener noreferrer');
assert.match(link.attrs['aria-label'], /新标签页/);
assert.match(link.innerHTML, /找片 \/ 追剧/);

vm.runInContext(script, context);
assert.equal(header.children.length, 1, 'reinjection adds no second entry');
header = newHeader();
observer();
assert.equal(header.children[0], link, 'SPA navigation gets the existing entry');
document.fullscreenElement = {};
events.get('fullscreenchange')();
assert.equal(link.hidden, true);
document.fullscreenElement = null;
videos = [{ getClientRects: () => [{}] }];
events.get('play')();
assert.equal(link.hidden, true, 'paused or playing visible video keeps the entry hidden');
videos = [];
observer();
assert.equal(link.hidden, false);
context.location.hash = '#!/login.html';
events.get('hashchange')();
assert.equal(link.hidden, true, 'login screens stay clear during session cleanup');
context.location.hash = '#!/home';
loggedIn = false;
events.get('viewshow')();
assert.equal(link.hidden, true, 'logout hides the entry');
context.ApiClient = undefined;
assert.doesNotThrow(observer, 'missing API never breaks Emby navigation');
assert.equal(link.hidden, true);
header = null;
observer();
assert.equal(link.parentElement, null);
assert.match(css, /\[hidden\].*display: none !important/);
assert.match(css, /:focus-visible/);
assert.match(css, /min-height: 44px/);
assert.doesNotMatch(css, /position:\s*(fixed|absolute)/, 'entry never overlays the player');
for (const hostname of ['127.0.0.1', 'localhost', '[::1]']) {
  const tunnel = vm.createContext({
    ...context, window: { addEventListener },
    location: { hash: '#!/home', hostname, protocol: 'http:' },
  });
  vm.runInContext(script, tunnel);
  assert.equal(link.href, `http://${hostname}:8097/discover/`, 'SSH tunnel reaches the custom portal directly');
}
console.log('search entry: login, logout, SPA, playback, safe navigation and reinjection checks passed');
