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

    // TLS: it's best to VERIFY. If your CA store is misconfigured, you can flip this to false temporarily,
    // but prefer fixing php.ini/curl CA instead of disabling verification.
    CURLOPT_SSL_VERIFYPEER => true,

    // Send cookies explicitly (we are not relying on jar parsing)
    CURLOPT_COOKIE         => $cookieHeader,

    // Optional cookie jar (Netscape format) if you want cURL to persist Set-Cookie from responses:
    // Use a .txt file, not JSON.
    CURLOPT_COOKIEJAR      => __DIR__ . '/cookies/ig_cookies.txt',
    CURLOPT_COOKIEFILE     => __DIR__ . '/cookies/ig_cookies.txt',

    // Headers that make us look like the web app
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

if (isset($data['reels'][$userId]['items']) && is_array($data['reels'][$userId]['items'])) {
    foreach ($data['reels'][$userId]['items'] as $it) {
        $caption = $it['caption']['text'] ?? '';
        $videoUrl = $it['video_versions'][0]['url'] ?? '';
        $takenAt  = isset($it['taken_at']) ? date('c', $it['taken_at']) : null;
        $out[] = [
            'id'           => $it['id'] ?? null,
            'caption'      => $caption,
            'video_url'    => $videoUrl,
            'taken_at_iso' => $takenAt,
        ];
    }
} else {
    $out = ['raw' => $data];
}

header('Content-Type: application/json; charset=utf-8');
echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
