<?php
// reels.php [user_id]
// Usage: php reels.php 47391282258

// --------------- CONFIG ---------------
$sessionid  = getenv('IG_SESSIONID') ?: '';
$ds_user_id = getenv('IG_DS_USER_ID') ?: '';
$csrf       = getenv('IG_CSRF') ?: '';
$userId     = $argv[1] ?? $ds_user_id;
$UA         = getenv('IG_UA') ?: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';

// fail fast on missing env
if (!$sessionid || !$ds_user_id || !$csrf) {
    fwrite(STDERR, "Missing env vars IG_SESSIONID / IG_DS_USER_ID / IG_CSRF\n");
    exit(1);
}
if (!$userId) {
    fwrite(STDERR, "Usage: php reels.php <user_id>\n");
    exit(1);
}

// ensure cookies dir exists
$cookiesDir = __DIR__ . '/cookies';
if (!is_dir($cookiesDir)) {
    @mkdir($cookiesDir, 0777, true);
}

// Build Cookie header EXACTLY like a browser would
$cookieHeader = "sessionid={$sessionid}; ds_user_id={$ds_user_id}; csrftoken={$csrf};";
$url = "https://www.instagram.com/api/v1/feed/reels_media/";

// Instagram accepts JSON array or bracketed string; JSON is safest:
$postFields = http_build_query([
    'user_ids' => json_encode([$userId], JSON_UNESCAPED_SLASHES),
]);

// --- cURL setup ---
$ch = curl_init($url);
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_POST           => true,
    CURLOPT_POSTFIELDS     => $postFields,
    CURLOPT_FOLLOWLOCATION => true,

    // Stability
    CURLOPT_HTTP_VERSION   => CURL_HTTP_VERSION_1_1, // avoid HTTP/2 stream issues
    CURLOPT_ENCODING       => 'gzip,deflate',        // decode compressed bodies
    CURLOPT_TIMEOUT        => 30,
    CURLOPT_CONNECTTIMEOUT => 15,

    // TLS: verify certificate (recommended)
    CURLOPT_SSL_VERIFYPEER => true,

    // Send cookies explicitly (we are not relying on jar parsing)
    CURLOPT_COOKIE         => $cookieHeader,

    // Optional cookie jar (Netscape format)
    CURLOPT_COOKIEJAR      => $cookiesDir . '/ig_cookies.txt',
    CURLOPT_COOKIEFILE     => $cookiesDir . '/ig_cookies.txt',

    // Headers that mimic the web app
    CURLOPT_HTTPHEADER     => [
        "User-Agent: {$UA}",
        "Referer: https://www.instagram.com/",
        "Accept-Language: en-US,en;q=0.9",
        "Accept-Encoding: gzip, deflate",
        "X-IG-App-ID: 936619743392459",
        "X-CSRFToken: {$csrf}",
        "X-Requested-With: XMLHttpRequest",
        "Content-Type: application/x-www-form-urlencoded",
        "Expect:", // disable 100-continue dance
    ],
]);

// --- tiny retry wrapper for flaky network / CDN hiccups ---
$attempts = 0;
$maxAttempts = 3;
do {
    $attempts++;
    $body = curl_exec($ch);
    $err  = curl_error($ch);
    $code = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);

    // Retry on transient network errors or proxy HTML "5xx" bodies
    $transient = $err && (stripos($err, 'STREAM_CLOSED') !== false || stripos($err, 'resolve host') !== false);
    $html5xx   = !$err && ($code >= 200 && $code < 500) && stripos((string)$body, '5xx Server Error') !== false;

    if (($transient || $html5xx) && $attempts < $maxAttempts) {
        usleep(400000 * $attempts); // 0.4s, 0.8s
        continue;
    }
    break;
} while ($attempts < $maxAttempts);

curl_close($ch);

// --- handle result ---
if ($err) {
    fwrite(STDERR, "cURL error: {$err}\n");
    exit(1);
}
if ($code >= 400) {
    fwrite(STDERR, "HTTP {$code} response:\n{$body}\n");
    exit(1);
}

$data = json_decode($body, true);
$out  = [];

// helper: safe getter
$gv = function(array $a, array $path, $default = null) {
    $cur = $a;
    foreach ($path as $k) {
        if (is_array($cur) && array_key_exists($k, $cur)) {
            $cur = $cur[$k];
        } else {
            return $default;
        }
    }
    return $cur;
};

if (isset($data['reels'][$userId]['items']) && is_array($data['reels'][$userId]['items'])) {
    foreach ($data['reels'][$userId]['items'] as $it) {
        // media basics
        $mediaType = $it['media_type'] ?? null; // 1=image, 2=video (varies by API)
        $imageUrl  = $gv($it, ['image_versions2','candidates',0,'url'], '');
        $videoUrl  = $gv($it, ['video_versions',0,'url'], '');
        $width     = $it['original_width']  ?? null;
        $height    = $it['original_height'] ?? null;
        $durationMs = isset($it['video_duration']) ? (int)round((float)$it['video_duration'] * 1000) : null;

        $takenAt   = isset($it['taken_at'])    ? date('c', $it['taken_at'])    : null;
        $expiring  = isset($it['expiring_at']) ? date('c', $it['expiring_at']) : null;

        // caption
        $caption = trim($gv($it, ['caption','text'], ''));

        // hashtags: API + caption
        $hashtags = [];
        // from API
        if (!empty($it['story_hashtags']) && is_array($it['story_hashtags'])) {
            foreach ($it['story_hashtags'] as $h) {
                $tag = $gv($h, ['hashtag','name']);
                if ($tag) { $hashtags[] = $tag; }
            }
        }
        // from caption
        if ($caption !== '') {
            if (preg_match_all('/#([\p{L}\p{N}_]+)/u', $caption, $mHash)) {
                foreach ($mHash[1] as $t) { $hashtags[] = $t; }
            }
        }
        $hashtags = array_values(array_unique($hashtags));

        // links from stickers (CTA/tappable/bloks)
        $linksSticker = [];
        // story_cta
        if (!empty($it['story_cta']) && is_array($it['story_cta'])) {
            foreach ($it['story_cta'] as $cta) {
                foreach (($cta['links'] ?? []) as $lnk) {
                    $u = $lnk['webUri'] ?? $lnk['url'] ?? null;
                    if ($u) { $linksSticker[] = trim($u); }
                }
            }
        }
        // tappable_objects / tappable_object
        $tappables = [];
        if (!empty($it['tappable_objects']) && is_array($it['tappable_objects'])) {
            $tappables = $it['tappable_objects'];
        } elseif (!empty($it['tappable_object']) && is_array($it['tappable_object'])) {
            $tappables = $it['tappable_object'];
        }
        foreach ($tappables as $to) {
            $u = $to['url'] ?? ($to['link'] ?? null) ?? ($gv($to, ['tap_state','url']) ?? null);
            if ($u) { $linksSticker[] = trim($u); }
        }
        // story_bloks_stickers
        if (!empty($it['story_bloks_stickers']) && is_array($it['story_bloks_stickers'])) {
            foreach ($it['story_bloks_stickers'] as $bs) {
                $u = $bs['url'] ?? ($gv($bs, ['tap_state','url']) ?? null);
                if ($u) { $linksSticker[] = trim($u); }
            }
        }

        // links from caption text
        $linksCaption = [];
        if ($caption !== '') {
            if (preg_match_all('~https?://[^\s\)\]]+~iu', $caption, $mLink)) {
                $linksCaption = array_map('trim', $mLink[0]);
            }
        }
        $links = array_values(array_unique(array_merge($linksSticker, $linksCaption)));

        // stickers grouped
        $stickers = [
            'mentions'       => [],
            'polls'          => $it['story_polls']      ?? [],
            'quizzes'        => $it['story_quiz']       ?? [],
            'sliders'        => $it['story_sliders']    ?? [],
            'questions'      => $it['story_questions']  ?? [],
            'music'          => [],
            'products'       => [],
            'locations'      => [],
            'countdowns'     => $it['story_countdowns'] ?? [],
            'paid_partnership'=> null,
        ];

        // mentions (@usernames)
        if (!empty($it['reel_mentions']) && is_array($it['reel_mentions'])) {
            foreach ($it['reel_mentions'] as $m) {
                $u = $gv($m, ['user','username']);
                if ($u) { $stickers['mentions'][] = $u; }
            }
        }
        $stickers['mentions'] = array_values(array_unique($stickers['mentions']));

        // music
        if (!empty($it['story_music_stickers'])) {
            foreach ($it['story_music_stickers'] as $ms) {
                $stickers['music'][] = [
                    'title'  => $gv($ms, ['music_asset_info','title']),
                    'artist' => $gv($ms, ['music_asset_info','artist']),
                ];
            }
        }

        // products (Shopping)
        if (!empty($it['story_product_stickers'])) {
            foreach ($it['story_product_stickers'] as $ps) {
                $p = $ps['product'] ?? [];
                $stickers['products'][] = [
                    'id'       => $p['id'] ?? null,
                    'name'     => $p['title'] ?? null,
                    'merchant' => $gv($p, ['merchant','username']),
                    'price'    => $p['current_price'] ?? null,
                ];
            }
        }

        // locations
        if (!empty($it['story_locations']) && is_array($it['story_locations'])) {
            foreach ($it['story_locations'] as $loc) {
                $l = $loc['location'] ?? [];
                $stickers['locations'][] = [
                    'name' => $l['name'] ?? null,
                    'city' => $l['city'] ?? null,
                    'lat'  => $l['lat'] ?? null,
                    'lng'  => $l['lng'] ?? null,
                ];
            }
        }

        // paid partnership (if present in various fields)
        if (!empty($it['branded_content_tag_info']['sponsor']['username'])) {
            $stickers['paid_partnership'] = [
                'partner_username' => $it['branded_content_tag_info']['sponsor']['username'],
            ];
        }

        // unified content object
        $content = [
            'caption'  => $caption,
            'links'    => $links,
            'hashtags' => $hashtags,
            'stickers' => $stickers,
        ];

        $out[] = [
            'id'              => $it['id'] ?? null,
            'taken_at_iso'    => $takenAt,
            'expiring_at_iso' => $expiring,
            'media_type'      => $mediaType,
            'image_url'       => $imageUrl,
            'video_url'       => $videoUrl,
            'duration_ms'     => $durationMs,
            'width'           => $width,
            'height'          => $height,
            'content'         => $content,
        ];
    }
} else {
    $out = ['raw' => $data];
}

header('Content-Type: application/json; charset=utf-8');
echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT), PHP_EOL;
