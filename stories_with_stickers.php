<?php
// stories_with_stickers.php
// Usage: php stories_with_stickers.php <USER_ID>
// Requires env: IG_SESSIONID, IG_CSRF, IG_DS_USER_ID  (optional: IG_UA)
// Output: array of objects in the exact schema requested
declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use GuzzleHttp\Client;
use GuzzleHttp\Handler\CurlHandler;
use GuzzleHttp\HandlerStack;
// --- fallback helpers when mbstring is missing ---
function u_lower(string $s): string {
    return function_exists('mb_strtolower') ? mb_strtolower($s, 'UTF-8') : strtolower($s);
}
function u_len(string $s): int {
    return function_exists('mb_strlen') ? mb_strlen($s, 'UTF-8') : strlen($s);
}

/** ----------------- helpers ----------------- */
function envs(string $k, ?string $def = ''): string {
    $v = getenv($k);
    return ($v === false) ? (string)$def : (string)$v;
}
function jdie(string $msg, int $code = 1): void {
    fwrite(STDERR, $msg . PHP_EOL);
    exit($code);
}
function firstNonEmpty(...$vals) {
    foreach ($vals as $v) { if ($v !== null && $v !== '') return $v; }
    return null;
}
function isHebrew(string $s): bool {
    return (bool)preg_match('/\p{Hebrew}/u', $s);
}
function langGuess(?string $text): ?string {
    if ($text === null || trim($text) === '') return null;
    if (isHebrew($text)) return 'he';
    // אם יש ASCII בעיקר – ננחש 'en'; אחרת null
    $letters = preg_replace('/[^A-Za-z]/', '', $text);
    if ($letters !== '' && strlen($letters) >= max(3, (int)(strlen($text) * 0.2))) return 'en';
    return null;
}
function uniqStrings(array $arr): array {
    $seen = [];
    $out  = [];
    foreach ($arr as $v) {
        $v = (string)$v;
        $k = u_lower(trim($v));   // במקום mb_strtolower
        if ($k !== '' && !isset($seen[$k])) {
            $seen[$k] = true;
            $out[]    = $v;
        }
    }
    return array_values($out);
}

function collectHashtagsFromCaption(?string $caption): array {
    if (!$caption) return [];
    if (preg_match_all('/#([\p{L}\p{N}_]+)/u', $caption, $m)) {
        return uniqStrings($m[1]);
    }
    return [];
}
function collectMentionsFromCaption(?string $caption): array {
    if (!$caption) return [];
    if (preg_match_all('/@([\p{L}\p{N}_.]+)/u', $caption, $m)) {
        return uniqStrings($m[1]);
    }
    return [];
}
function resolveDomain(?string $url): ?string {
    if (!$url) return null;
    $host = parse_url($url, PHP_URL_HOST);
    if (is_string($host) && $host !== '') return $host;
    return null;
}
function toIso(?int $ts): ?string {
    if (!$ts) return null;
    return date('c', $ts);
}
function bboxOrDefault(?array $src): array {
    if (!$src) return [0.0, 0.0, 0.0, 0.0];
    $x = isset($src['x'])      ? (float)$src['x']      : 0.0;
    $y = isset($src['y'])      ? (float)$src['y']      : 0.0;
    $w = isset($src['width'])  ? (float)$src['width']  : 0.0;
    $h = isset($src['height']) ? (float)$src['height'] : 0.0;
    return [$x, $y, $w, $h];
}

/** sticker text normalizer for classification */
// היה: function stickerTextOf(array $src): string {
function stickerTextOf(?array $src): string {
    if (!$src) return '';
    $candidates = [
        $src['title'] ?? null,
        $src['text'] ?? null,
        $src['name'] ?? null,
        $src['question'] ?? null,
    ];
    $t = firstNonEmpty(...$candidates);
    return (string)($t ?? '');
}

function classifySticker(?string $text, ?string $url = null): string {
    $t = u_lower($text ?? '');  // במקום mb_strtolower

    if ($url) return 'url';
    if ($t === '') return 'generic';

    // price / percent / coupon / date
    if (preg_match('/(?:^|[\s])(?:₪|\$|€)\s*\d+(?:[.,]\d+)?/u', $t) ||
        preg_match('/\d+(?:[.,]\d+)?\s*(?:₪|ש"ח|\$|€)/u', $t)) {
        return 'price';
    }
    if (preg_match('/\b\d{1,3}\s?%\b/u', $t)) {
        return 'percent';
    }
    if (preg_match('/\b(coupon|קופון|promo|voucher)\b/u', $t)) {
        return 'coupon';
    }
    if (preg_match('/\b(20\d{2}|19\d{2})[-\/\.](0?[1-9]|1[0-2])[-\/\.](0?[1-9]|[12]\d|3[01])\b/u', $t)) {
        return 'date';
    }
    return 'generic';
}

/** ----------------- inputs/env ----------------- */
$uid = $argv[1] ?? null;
if (!$uid) jdie("Usage: php stories_with_stickers.php <USER_ID>", 2);

$csrf = envs('IG_CSRF', null);
$sess = envs('IG_SESSIONID', null);
$dsid = envs('IG_DS_USER_ID', null);
$ua   = envs('IG_UA', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36');

if (!$csrf || !$sess || !$dsid) jdie("Missing env: IG_CSRF / IG_SESSIONID / IG_DS_USER_ID", 3);

/** ----------------- Guzzle client ----------------- */
$handler = HandlerStack::create(new CurlHandler());
$verifyPath = ini_get('curl.cainfo');
if (!$verifyPath) $verifyPath = ini_get('openssl.cafile') ?: true;

$client = new Client([
    'base_uri'         => 'https://www.instagram.com/',
    'handler'          => $handler,
    'http_errors'      => false,
    'timeout'          => 30,
    'decode_content'   => true,
    'verify'           => $verifyPath,
    'force_ip_resolve' => 'v4',
    'curl'             => [CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_1_1],
    'headers' => [
        'User-Agent'        => $ua,
        'Referer'           => 'https://www.instagram.com/',
        'Origin'            => 'https://www.instagram.com',
        'Accept'            => '*/*',
        'Accept-Language'   => 'en-US,en;q=0.9',
        'Accept-Encoding'   => 'gzip, deflate',
        'X-Requested-With'  => 'XMLHttpRequest',
        'X-IG-App-ID'       => '936619743392459',
        'X-CSRFToken'       => $csrf,
        'Cookie'            => "csrftoken={$csrf}; sessionid={$sess}; ds_user_id={$dsid};",
        'Sec-Fetch-Site'    => 'same-origin',
        'Sec-Fetch-Mode'    => 'cors',
        'Sec-Fetch-Dest'    => 'empty',
    ],
]);

/** ----------------- call API ----------------- */
$res  = $client->post('api/v1/feed/reels_media/', [
    'form_params' => [
        'user_ids' => json_encode([(string)$uid], JSON_UNESCAPED_SLASHES),
    ],
]);
$code = $res->getStatusCode();
$body = (string)$res->getBody();

if ($code !== 200) jdie("HTTP {$code} response:\n{$body}", 1);

$j = json_decode($body, true);
if (!is_array($j)) jdie("Bad JSON\n{$body}", 1);

/** ----------------- transform to requested schema ----------------- */
$out = [];
$tray = $j['reels'][(string)$uid]['items'] ?? ($j['reels_media'][0]['items'] ?? ($j['items'] ?? []));

/** Gather helper from IG objects into schema fields */
foreach ($tray as $it) {
    // media basics
    $mediaId  = $it['id'] ?? null;
    $owner    = $it['user'] ?? [];
    $userPk   = firstNonEmpty($owner['pk'] ?? null, $owner['pk_id'] ?? null, $owner['id'] ?? null);
    $username = $owner['username'] ?? null;

    $width  = $it['original_width']  ?? null;
    $height = $it['original_height'] ?? null;
    $durMs  = null;
    if (isset($it['video_duration'])) $durMs = (int)round((float)$it['video_duration'] * 1000);

    $imageUrl = firstNonEmpty(
        $it['image_versions2']['candidates'][0]['url'] ?? null,
        $it['display_url'] ?? null,
        $it['image_url'] ?? null
    );
    $videoUrl = firstNonEmpty(
        $it['video_versions'][0]['url'] ?? null,
        $it['video_url'] ?? null
    );

    // caption text
    $captionText = null;
    if (isset($it['caption'])) {
        $captionText = is_array($it['caption']) ? ($it['caption']['text'] ?? null) : (string)$it['caption'];
        if ($captionText !== null) $captionText = trim($captionText);
    }

    // expiring / taken
    $takenAtIso   = toIso($it['taken_at']    ?? null);
    $expiringIso  = toIso($it['expiring_at'] ?? null);

    // type heuristic (this endpoint מחזיר "סטוריז"; נשמור "story", ואם יש וידאו קצר – לא נכריז 'reel' בלי קוד ייעודי)
    $type = 'story';
    if (!empty($videoUrl) && ($height && $width) && $height > $width) {
        // אם תרצי להבדיל reel אמיתי – צריך מזהה קאנוני של reel (לא זמין כאן בד"כ)
        $type = 'story'; // נשאיר "story" כברירת מחדל בטוחה
    }

    // URLs: מהסטיקרים + מהכיתוב
    $urls = [];

    // story_cta / link stickers
    if (!empty($it['story_cta'])) {
        foreach ($it['story_cta'] as $cta) {
            foreach (($cta['links'] ?? []) as $lnk) {
                $u = $lnk['webUri'] ?? ($lnk['url'] ?? null);
                if ($u) $urls[] = ['text' => trim($u), 'resolved_domain' => resolveDomain($u)];
            }
        }
    }
    if (!empty($it['story_link_stickers'])) {
        foreach ($it['story_link_stickers'] as $ls) {
            $u = $ls['url'] ?? ($ls['link_url'] ?? null);
            if ($u) $urls[] = ['text' => trim($u), 'resolved_domain' => resolveDomain($u)];
        }
    }
    foreach (($it['tappable_objects'] ?? []) as $to) {
        if (($to['object_type'] ?? '') === 'link') {
            $u = $to['link']['url'] ?? ($to['url'] ?? null);
            if ($u) $urls[] = ['text' => trim($u), 'resolved_domain' => resolveDomain($u)];
        }
    }
    // from caption text
    if ($captionText) {
        if (preg_match_all('~https?://[^\s\)\]]+~iu', $captionText, $mLink)) {
            foreach ($mLink[0] as $u) {
                $u = trim($u);
                $urls[] = ['text' => $u, 'resolved_domain' => resolveDomain($u)];
            }
        }
    }
    // uniq urls
    $urls = array_values(array_reduce($urls, function($acc, $item){
        $k = strtolower($item['text']);
        $acc[$k] = $item;
        return $acc;
    }, []));

    // hashtags & mentions
    $hashtags = [];
    if (!empty($it['story_hashtags'])) {
        foreach ($it['story_hashtags'] as $h) {
            $name = $h['hashtag']['name'] ?? null;
            if ($name) $hashtags[] = $name;
        }
    }
    $hashtags = uniqStrings(array_merge($hashtags, collectHashtagsFromCaption($captionText)));

    $mentions = [];
    if (!empty($it['reel_mentions'])) {
        foreach ($it['reel_mentions'] as $m) {
            $u = $m['user']['username'] ?? null;
            if ($u) $mentions[] = $u;
        }
    }
    // tappable mention
    foreach (($it['tappable_objects'] ?? []) as $to) {
        if (($to['object_type'] ?? '') === 'mention') {
            $u = $to['user']['username'] ?? ($to['username'] ?? null);
            if ($u) $mentions[] = $u;
        }
    }
    $mentions = uniqStrings(array_merge($mentions, collectMentionsFromCaption($captionText)));

    // stickers -> your generic schema
    $stickers = [];

    // story_cta
    if (!empty($it['story_cta'])) {
        foreach ($it['story_cta'] as $cta) {
            foreach (($cta['links'] ?? []) as $lnk) {
                $u = $lnk['webUri'] ?? ($lnk['url'] ?? null);
                $text = stickerTextOf($lnk);
                if ($u || $text) {
                    $stickers[] = [
                        'type'       => classifySticker($text, $u),
                        'text'       => $text ?: ($u ?? ''),
                        'bbox'       => [0,0,0,0], // IG לא נותן bbox במבנה הזה
                        'confidence' => 0.0,
                    ];
                }
            }
        }
    }
    // story_link_stickers
    if (!empty($it['story_link_stickers'])) {
        foreach ($it['story_link_stickers'] as $ls) {
            $u = $ls['url'] ?? ($ls['link_url'] ?? null);
            $text = stickerTextOf($ls);
            $stickers[] = [
                'type'       => classifySticker($text, $u),
                'text'       => $text ?: ($u ?? ''),
                'bbox'       => bboxOrDefault($ls),
                'confidence' => 0.0,
            ];
        }
    }
    // tappable_objects (map common types)
    foreach (($it['tappable_objects'] ?? []) as $to) {
        $typeObj = $to['object_type'] ?? '';
        $text = stickerTextOf($to);
        $u    = $to['link']['url'] ?? ($to['url'] ?? null);
        if ($typeObj === 'link' || $u) {
            $stickers[] = [
                'type'       => 'url',
                'text'       => $u ?: $text,
                'bbox'       => bboxOrDefault($to),
                'confidence' => 0.0,
            ];
        } else {
            // classify by text only
            if ($text !== '') {
                $stickers[] = [
                    'type'       => classifySticker($text, null),
                    'text'       => $text,
                    'bbox'       => bboxOrDefault($to),
                    'confidence' => 0.0,
                ];
            }
        }
    }
    // polls/quizzes/sliders/questions → כסטיקר generic עם טקסט
    foreach (($it['story_polls'] ?? []) as $p) {
        $s = $p['poll_sticker'] ?? [];
        $text = trim(($s['question'] ?? '') . ' ' . implode(' ', array_map(fn($t) => $t['text'] ?? '', $s['tallies'] ?? [])));
        if ($text !== '') $stickers[] = ['type'=>'generic','text'=>$text,'bbox'=>bboxOrDefault($s),'confidence'=>0.0];
    }
    foreach (($it['story_sliders'] ?? []) as $s) {
        $st = $s['slider_sticker'] ?? [];
        $text = trim(($st['question'] ?? '') . ' ' . ($st['emoji'] ?? ''));
        if ($text !== '') $stickers[] = ['type'=>'generic','text'=>$text,'bbox'=>bboxOrDefault($st),'confidence'=>0.0];
    }
    $quizArr = $it['story_quizs'] ?? ($it['story_quiz'] ?? []);
    foreach ($quizArr as $q) {
        $st = $q['quiz_sticker'] ?? [];
        $choices = array_map(fn($t) => $t['text'] ?? '', $st['tallies'] ?? []);
        $text = trim(($st['question'] ?? '') . ' ' . implode(' ', $choices));
        if ($text !== '') $stickers[] = ['type'=>'generic','text'=>$text,'bbox'=>bboxOrDefault($st),'confidence'=>0.0];
    }
    foreach (($it['story_questions'] ?? []) as $q) {
        $st = $q['question_sticker'] ?? [];
        $text = $st['question'] ?? ($st['question_text'] ?? '');
        if ($text !== '') $stickers[] = ['type'=>'generic','text'=>$text,'bbox'=>bboxOrDefault($st),'confidence'=>0.0];
    }

    // raw_text_candidates (caption + sticker texts)
    $rawTextCandidates = [];
    if ($captionText) $rawTextCandidates[] = $captionText;
    foreach ($stickers as $s) { if (!empty($s['text'])) $rawTextCandidates[] = (string)$s['text']; }
    $rawTextCandidates = uniqStrings($rawTextCandidates);

    // frames_used heuristic: אם יש וידאו — נסמן [0, מינ(45ש׳׳, סוף), מינ(90ש׳׳, סוף)]
    $framesUsed = [];
    if ($durMs && $durMs > 0) {
        $framesUsed[] = 0;
        $framesUsed[] = min(45000, max(0, $durMs - 1));
        $framesUsed[] = min(90000, max(0, $durMs - 1));
        $framesUsed = array_values(array_unique($framesUsed));
    }

    // ocr (not enabled)
    $ocrText = null;
    $ocrConf = 0.0;
    $processingErrors = [];
    $processingErrors[] = 'ocr_not_enabled';

    // language guess (caption או טקסט גולמי ראשון)
    $lang = langGuess($captionText ?? ($rawTextCandidates[0] ?? null));

    // source_flags
    $hasText     = ($captionText !== null && $captionText !== '') || !empty($rawTextCandidates);
    $hasStickers = !empty($stickers);
    $hasLogoHint = false;

    // content hash
    $hashBase = json_encode([
        'media_id' => $mediaId,
        'caption'  => $captionText,
        'urls'     => array_map(fn($u)=>$u['text'], $urls),
        'hashtags' => $hashtags,
        'mentions' => $mentions,
    ], JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES);
    $contentHash = sha1((string)$hashBase);

    // permalink (לסטוריז אין פרמלינק ציבורי בדרך כלל) → null
    $permalink = null;

    // username fallback
    if (!$username && isset($it['user']['username'])) $username = (string)$it['user']['username'];

    // בנה את האובייקט הסופי לפי הסכימה שלך:
    $obj = [
        "media_id"        => (string)($mediaId ?? ''),
        "user_id"         => $userPk ? (string)$userPk : (string)$uid,
        "username"        => $username ?? null,
        "type"            => $type, // "story" (או "reel" אם תבחרי היגיון אחר)
        "taken_at_iso"    => $takenAtIso,
        "expiring_at_iso" => $expiringIso,

        "permalink"       => $permalink,
        "image_url"       => $imageUrl ?? null,
        "video_url"       => $videoUrl ?? null,

        "caption_text"    => $captionText ?? null,
        "ocr_text"        => $ocrText,
        "ocr_confidence"  => (float)$ocrConf,

        "stickers"        => $stickers, // כבר בפורמט: {type,text,bbox,confidence}

        "urls"            => $urls,     // [{text,resolved_domain}]
        "raw_text_candidates" => $rawTextCandidates,
        "hashtags"        => $hashtags,
        "mentions"        => $mentions,

        "frames_used"     => $framesUsed,
        "media_meta"      => [
            "width"       => (int)($width ?? 0),
            "height"      => (int)($height ?? 0),
            "duration_ms" => (int)($durMs ?? 0),
        ],

        "language_guess"  => $lang,
        "brand_candidates" => [], // אפשר להרחיב בהמשך (זיהוי מותגים)
        "source_flags"    => [
            "has_text"      => (bool)$hasText,
            "has_stickers"  => (bool)$hasStickers,
            "has_logo_hint" => (bool)$hasLogoHint,
        ],

        "content_hash"    => $contentHash,
        "processing"      => [
            "extraction_version" => "1.0.0",
            "errors"             => $processingErrors,
        ],
    ];

    $out[] = $obj;
}

/** output */
header('Content-Type: application/json; charset=utf-8');
echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT), PHP_EOL;
