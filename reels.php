<?php
// reels.php

$USER_ID = "47391282258"; // ה-ID של המשתמש
$UA = getenv('IG_EXACT_UA'); // אותו User-Agent שהגדרת
$COOKIE_FILE = __DIR__ . "/cookies/ig_cookies.json"; // הקובץ עם הקוקיז שהוצאת מהדפדפן

$ch = curl_init("https://www.instagram.com/api/v1/feed/reels_media/");

$postFields = http_build_query([
    'user_ids' => "[$USER_ID]"  // חשוב עם סוגריים מרובעים כמו באינסטגרם
]);

curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_POST => true,
    CURLOPT_POSTFIELDS => $postFields,
    CURLOPT_HTTPHEADER => [
        "User-Agent: $UA",
        "X-IG-App-ID: 936619743392459",
        "Content-Type: application/x-www-form-urlencoded",
    ],
    CURLOPT_COOKIEFILE => $COOKIE_FILE,
    CURLOPT_COOKIEJAR  => $COOKIE_FILE,
]);

$response = curl_exec($ch);
if ($response === false) {
    echo "cURL error: " . curl_error($ch);
} else {
    echo $response;
}
curl_close($ch);
