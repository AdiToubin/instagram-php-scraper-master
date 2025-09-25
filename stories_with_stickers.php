<?php
// stories_with_stickers.php
// שימוש: php stories_with_stickers.php <USER_ID>
// דורש שהקוקיז (IG_SESSIONID, IG_CSRF, IG_DS_USER_ID, IG_UA) יהיו בסביבת ה־CMD
declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';
use GuzzleHttp\Client;

function env(string $k, string $d=''){ $v=getenv($k); return $v===false?$d:$v; }

$uid = $argv[1] ?? null;
if (!$uid) { fwrite(STDERR,"Usage: php stories_with_stickers.php <USER_ID>\n"); exit(2); }

$ua   = env('IG_UA','Mozilla/5.0');
$csrf = env('IG_CSRF');
$sess = env('IG_SESSIONID');
$dsid = env('IG_DS_USER_ID');

if (!$csrf || !$sess || !$dsid) {
  fwrite(STDERR, "Missing env: IG_CSRF / IG_SESSIONID / IG_DS_USER_ID\n");
  exit(3);
}

$client = new Client([
  'base_uri' => 'https://www.instagram.com/',
  'headers' => [
    'User-Agent'        => $ua,
    'X-Requested-With'  => 'XMLHttpRequest',
    'X-CSRFToken'       => $csrf,
    'Referer'           => 'https://www.instagram.com/',
    'Accept'            => 'application/json',
    'Cookie'            => "csrftoken={$csrf}; sessionid={$sess}; ds_user_id={$dsid};",
  ],
  'http_errors' => false,
  'timeout' => 30,
]);

$res = $client->get('api/v1/feed/reels_media/', ['query' => ['reel_ids' => $uid]]);
$code = $res->getStatusCode();
$body = (string)$res->getBody();
if ($code !== 200) {
  fwrite(STDERR, "HTTP $code response:\n$body\n");
  exit(1);
}
$j = json_decode($body, true);
if (!is_array($j)) {
  fwrite(STDERR, "Bad JSON\n$body\n");
  exit(1);
}

/** ---------- Helpers to safely extract stickers ---------- */
function mapMentions(array $item): array {
  $out = [];
  foreach (($item['reel_mentions'] ?? []) as $m) {
    $u = $m['user']['username'] ?? null;
    if ($u) $out[] = [
      'username' => $u,
      'x' => $m['x'] ?? null, 'y' => $m['y'] ?? null, 'w' => $m['width'] ?? null, 'h' => $m['height'] ?? null
    ];
  }
  return $out;
}

function mapHashtags(array $item): array {
  $out = [];
  foreach (($item['story_hashtags'] ?? []) as $h) {
    $name = $h['hashtag']['name'] ?? null;
    if ($name) $out[] = [
      'tag' => $name,
      'x' => $h['x'] ?? null, 'y' => $h['y'] ?? null
    ];
  }
  return $out;
}

function mapLocations(array $item): array {
  $out = [];
  foreach (($item['story_locations'] ?? []) as $loc) {
    $name = $loc['location']['name'] ?? null;
    if ($name) $out[] = [
      'name' => $name,
      'lat' => $loc['location']['lat'] ?? null,
      'lng' => $loc['location']['lng'] ?? null
    ];
  }
  return $out;
}

function mapLinks(array $item): array {
  $out = [];
  // link sticker חדש/ישן
  if (!empty($item['story_cta'])) {
    foreach ($item['story_cta'] as $cta) {
      foreach (($cta['links'] ?? []) as $lnk) {
        $uri = $lnk['webUri'] ?? $lnk['url'] ?? null;
        if ($uri) $out[] = ['url' => $uri, 'title' => $lnk['title'] ?? null];
      }
    }
  }
  if (!empty($item['story_link_stickers'])) {
    foreach ($item['story_link_stickers'] as $ls) {
      $uri = $ls['url'] ?? $ls['link_url'] ?? null;
      if ($uri) $out[] = ['url' => $uri, 'title' => $ls['title'] ?? null];
    }
  }
  // גם בתוך tappable_objects לפעמים יש קישורים
  foreach (($item['tappable_objects'] ?? []) as $to) {
    if (($to['object_type'] ?? '') === 'link' && !empty($to['link'])) {
      $uri = $to['link']['url'] ?? null;
      if ($uri) $out[] = ['url' => $uri, 'title' => $to['title'] ?? null];
    }
  }
  return $out;
}

function mapPolls(array $item): array {
  $out = [];
  foreach (($item['story_polls'] ?? []) as $p) {
    $s = $p['poll_sticker'] ?? [];
    $opts = [];
    foreach (($s['tallies'] ?? []) as $t) { $opts[] = $t['text'] ?? ''; }
    $out[] = [
      'id' => $s['poll_id'] ?? null,
      'question' => $s['question'] ?? '',
      'options' => $opts
    ];
  }
  return $out;
}

function mapSliders(array $item): array {
  $out = [];
  foreach (($item['story_sliders'] ?? []) as $s) {
    $st = $s['slider_sticker'] ?? [];
    $out[] = [
      'question' => $st['question'] ?? '',
      'emoji'    => $st['emoji'] ?? ''
    ];
  }
  return $out;
}

function mapQuizzes(array $item): array {
  $out = [];
  // לפעמים המפתח הוא story_quizs / story_quiz
  $arr = $item['story_quizs'] ?? ($item['story_quiz'] ?? []);
  foreach ($arr as $q) {
    $st = $q['quiz_sticker'] ?? [];
    $choices = [];
    foreach (($st['tallies'] ?? []) as $t) { $choices[] = $t['text'] ?? ''; }
    $out[] = [
      'question' => $st['question'] ?? '',
      'choices'  => $choices,
      'correct_choice' => $st['correct_answer'] ?? null,
    ];
  }
  return $out;
}

function mapQuestions(array $item): array {
  $out = [];
  foreach (($item['story_questions'] ?? []) as $q) {
    $st = $q['question_sticker'] ?? [];
    $out[] = [ 'prompt' => $st['question'] ?? ($st['question_text'] ?? '') ];
  }
  return $out;
}

function mapProducts(array $item): array {
  $out = [];
  foreach (($item['story_product_items'] ?? []) as $p) {
    $prod = $p['product_item'] ?? [];
    $out[] = [
      'name' => $prod['title'] ?? ($prod['name'] ?? ''),
      'merchant' => $prod['merchant']['username'] ?? null,
      'price' => $prod['current_price'] ?? null,
      'product_id' => $prod['id'] ?? null
    ];
  }
  return $out;
}

function mapMusic(array $item): ?array {
  // לפעמים music metadata מופיע תחת 'music_metadata'/'audio'
  $m = $item['music_metadata'] ?? ($item['audio'] ?? null);
  if (!$m) return null;
  return [
    'title'  => $m['music_title'] ?? ($m['title'] ?? null),
    'artist' => $m['music_artist'] ?? ($m['artist'] ?? null),
  ];
}

function videoUrl(array $item): string {
  if (!empty($item['video_versions'][0]['url'])) return $item['video_versions'][0]['url'];
  if (!empty($item['video_url'])) return $item['video_url']; // אם הספרייה שלך כבר ממפה
  return '';
}
function imageUrl(array $item): string {
  if (!empty($item['image_versions2']['candidates'][0]['url'])) return $item['image_versions2']['candidates'][0]['url'];
  if (!empty($item['display_url'])) return $item['display_url'];
  if (!empty($item['image_url'])) return $item['image_url'];
  return '';
}

/** ---------- Build output ---------- */
$out = [];
$tray = $j['reels'][ (string)$uid ]['items'] ?? ($j['reels_media'][0]['items'] ?? ($j['items'] ?? []));
foreach ($tray as $it) {
  $taken_at = $it['taken_at'] ?? null;
  $iso = null;
  if ($taken_at) $iso = date('c', is_numeric($taken_at) ? (int)$taken_at : strtotime((string)$taken_at));

  $out[] = [
    'id'           => $it['id'] ?? '',
    'type'         => !empty(videoUrl($it)) ? 'video_story' : 'image_story',
    'taken_at_iso' => $iso,
    'caption'      => $it['caption']['text'] ?? ($it['caption'] ?? ''),
    'video_url'    => videoUrl($it),
    'image_url'    => imageUrl($it),
    'stickers'     => [
      'mentions'  => mapMentions($it),
      'hashtags'  => mapHashtags($it),
      'locations' => mapLocations($it),
      'links'     => mapLinks($it),
      'polls'     => mapPolls($it),
      'sliders'   => mapSliders($it),
      'quizzes'   => mapQuizzes($it),
      'questions' => mapQuestions($it),
      'products'  => mapProducts($it),
      'music'     => mapMusic($it),
    ],
  ];
}

echo json_encode($out, JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT);
