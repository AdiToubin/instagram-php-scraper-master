<?php
// reels.php [user_id]
// שימוש: php reels.php 47391282258

$sessionid = getenv('IG_SESSIONID') ?: '';
$ds_user_id = getenv('IG_DS_USER_ID') ?: '';
$csrf = getenv('IG_CSRF') ?: '';
$userId = $argv[1] ?? $ds_user_id;
$UA = getenv('IG_UA') ?: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';

if (!$sessionid || !$ds_user_id || !$csrf) {
    http_response_code(500);
    die("Missing env vars IG_SESSIONID / IG_DS_USER_ID / IG_CSRF\n");
}
if (!$userId) {
    die("Usage: php reels.php <user_id>\n");
}

// בונים כותרת Cookie בדיוק כמו בדפדפן
$cookies = "sessionid={$sessionid}; ds_user_id={$ds_user_id}; csrftoken={$csrf};";

$url = "https://www.instagram.com/api/v1/feed/reels_media/?user_ids=[" . $userId . "]";

$ch = curl_init($url);
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => true,
    CURLOPT_HTTPHEADER => [
        'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118 Safari/537.36',
        'Accept: application/json, text/plain, */*',
        'Referer: https://www.instagram.com/',
        "Cookie: {$cookies}",
        "X-CSRFToken: {$csrf}",
    ],
]);
$body = curl_exec($ch);
$err  = curl_error($ch);
$code = curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
curl_close($ch);

if ($err) {
    die("cURL error: {$err}\n");
}
if ($code >= 400) {
    die("HTTP {$code} response:\n{$body}\n");
}

// ממפים לתוצאה נקייה (id, כיתוב, וידאו, תאריך) אם אפשר
$data = json_decode($body, true);
$out = [];
if (isset($data['reels'][$userId]['items'])) {
    foreach ($data['reels'][$userId]['items'] as $it) {
        $caption = $it['caption']['text'] ?? '';
        $videoUrl = $it['video_versions'][0]['url'] ?? '';
        $takenAt  = isset($it['taken_at']) ? date('c', $it['taken_at']) : null;
        $out[] = [
            'id' => $it['id'] ?? null,
            'caption' => $caption,
            'video_url' => $videoUrl,
            'taken_at_iso' => $takenAt,
        ];
    }
} else {
    // אם המבנה שונה – מחזירים את התשובה הגולמית לעיונך
    $out = ['raw' => $data];
}

header('Content-Type: application/json; charset=utf-8');
echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT);
