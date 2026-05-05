<?php
// highlights_extractor.php
// Usage: php highlights_extractor.php <USER_ID> [HIGHLIGHT_ID]
// If HIGHLIGHT_ID is provided, extracts specific highlight. Otherwise lists all highlights.
// Env required: IG_SESSIONID, IG_CSRF, IG_DS_USER_ID
// Optional: IG_UA, TESSERACT_PATH (for OCR), FFMPEG_PATH (video frame OCR), OCR_LANGS (e.g. "heb+eng")
// Optional: IG_WWW_CLAIM, IG_DEBUG=1 (dump highlights_debug.json)
declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use GuzzleHttp\Client;
use GuzzleHttp\Handler\CurlHandler;
use GuzzleHttp\HandlerStack;

/* ---------- Load environment variables ---------- */
if (file_exists(__DIR__ . '/.env')) {
    $lines = file(__DIR__ . '/.env', FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    foreach ($lines as $line) {
        if (strpos(trim($line), '#') === 0) continue; // Skip comments
        if (strpos($line, '=') !== false) {
            list($name, $value) = explode('=', $line, 2);
            $name = trim($name);
            $value = trim($value);
            if (!empty($name) && !getenv($name)) {
                putenv("$name=$value");
            }
        }
    }
}

/* ---------- Include shared functions from stories script ---------- */
// We'll reuse the helper functions from the stories script
require_once __DIR__ . '/stories_functions.php';

/* ---------- inputs/env ---------- */
$uid = $argv[1] ?? null;
$highlightId = $argv[2] ?? null;

if (!$uid) jdie("Usage: php highlights_extractor.php <USER_ID> [HIGHLIGHT_ID]", 2);

$csrf=envs('IG_CSRF',null); $sess=envs('IG_SESSIONID',null); $dsid=envs('IG_DS_USER_ID',null);
$ua  =envs('IG_UA','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36');
if(!$csrf||!$sess||!$dsid) jdie("Missing env: IG_CSRF / IG_SESSIONID / IG_DS_USER_ID",3);

$handler=HandlerStack::create(new CurlHandler());
$verifyPath=ini_get('curl.cainfo'); if(!$verifyPath) $verifyPath=ini_get('openssl.cafile')?:true;
$client=new Client([
  'base_uri'=>'https://www.instagram.com/','handler'=>$handler,'http_errors'=>false,'timeout'=>30,
  'decode_content'=>true,'verify'=>$verifyPath,'force_ip_resolve'=>'v4','curl'=>[CURLOPT_HTTP_VERSION=>CURL_HTTP_VERSION_1_1],
  'headers'=>[
    'User-Agent'=>$ua,
    'Referer'=>'https://www.instagram.com/',
    'Origin'=>'https://www.instagram.com',
    'Accept'=>'*/*',
    'Accept-Language'=>'en-US,en;q=0.9',
    'Accept-Encoding'=>'gzip, deflate',
    'X-Requested-With'=>'XMLHttpRequest',
    'X-IG-App-ID'=>'936619743392459',
    'X-CSRFToken'=>$csrf,
    'Cookie'=>"csrftoken={$csrf}; sessionid={$sess}; ds_user_id={$dsid};",
    'Sec-Fetch-Site'=>'same-origin','Sec-Fetch-Mode'=>'cors','Sec-Fetch-Dest'=>'empty',
  ],
]);

$debugOn = envs('IG_DEBUG','')==='1';
$out = [];

if (!$highlightId) {
    /* ---------- Get all highlights for user and their content ---------- */
    $res = $client->get("api/v1/highlights/{$uid}/highlights_tray/");
    $code = $res->getStatusCode();
    $body = (string)$res->getBody();

    if ($code !== 200) jdie("HTTP {$code} response:\n{$body}", 1);

    $j = json_decode($body, true);
    if (!is_array($j)) jdie("Bad JSON\n{$body}", 1);

    if ($debugOn) {
        @file_put_contents(__DIR__.'/highlights_debug.json', json_encode($j, JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT));
    }

    // Extract all highlights and their content
    $highlights = $j['tray'] ?? [];
    foreach ($highlights as $highlight) {
        $fullHighlightId = $highlight['id'] ?? '';
        $highlightId = str_replace('highlight:', '', $fullHighlightId); // Remove prefix if exists
        $highlightTitle = $highlight['title'] ?? '';

        if ($debugOn) {
            error_log("Processing highlight: {$highlightTitle} (ID: {$fullHighlightId} -> {$highlightId})");
        }

        // Try multiple API endpoints for highlight content
        $endpoints = [
            "api/v1/feed/reels_media/?reel_ids=highlight%3A{$highlightId}",
            "api/v1/feed/reels_media/?reel_ids={$fullHighlightId}",
            "graphql/query/?query_hash=45246d3fe16ccc6577e0bd297a5db1ab&variables=" . urlencode(json_encode(['reel_ids' => [$fullHighlightId], 'tag_names' => [], 'location_ids' => [], 'highlight_reel_ids' => [$highlightId], 'precomposed_overlay' => false, 'show_story_viewer_list' => true, 'story_viewer_fetch_count' => 50, 'story_viewer_cursor' => '', 'stories_video_dash_manifest' => false])),
            "api/v1/highlights/{$highlightId}/",
            "api/v1/highlights/{$fullHighlightId}/",
            "api/v1/feed/reels_media/?reel_ids={$highlightId}",
        ];

        $contentCode = 0;
        $contentBody = '';
        $contentJ = null;

        foreach ($endpoints as $endpoint) {
            $contentRes = $client->get($endpoint);
            $contentCode = $contentRes->getStatusCode();
            $contentBody = (string)$contentRes->getBody();

            if ($debugOn) {
                error_log("Trying endpoint: {$endpoint} -> HTTP {$contentCode}");
            }

            if ($contentCode === 200) {
                $tempJ = json_decode($contentBody, true);
                if (is_array($tempJ)) {
                    // Check if we have actual content (not empty arrays)
                    $hasContent = false;
                    if (!empty($tempJ['reels']) && is_array($tempJ['reels'])) {
                        foreach ($tempJ['reels'] as $reel) {
                            if (!empty($reel) && isset($reel['items']) && !empty($reel['items'])) {
                                $hasContent = true;
                                break;
                            }
                        }
                    }
                    if (!empty($tempJ['data']['reels_media']) && is_array($tempJ['data']['reels_media'])) {
                        foreach ($tempJ['data']['reels_media'] as $reel) {
                            if (!empty($reel) && isset($reel['items']) && !empty($reel['items'])) {
                                $hasContent = true;
                                break;
                            }
                        }
                    }
                    if (!empty($tempJ['items'])) {
                        $hasContent = true;
                    }

                    if ($hasContent) {
                        $contentJ = $tempJ;
                        if ($debugOn) {
                            error_log("Success with endpoint: {$endpoint}");
                        }
                        break;
                    }
                }
            }

            // Small delay between attempts
            usleep(200000); // 0.2 seconds
        }

        if ($debugOn) {
            error_log("Fetching highlight {$highlightId} ({$highlightTitle}): HTTP {$contentCode}");
        }

        if ($contentCode === 200) {
            $contentJ = json_decode($contentBody, true);
            if (is_array($contentJ)) {
                if ($debugOn) {
                    // Debug: save the response to see what we're getting
                    @file_put_contents(__DIR__."/debug_highlight_{$highlightId}.json", json_encode($contentJ, JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT));
                    error_log("Available keys in response: " . implode(', ', array_keys($contentJ)));
                    if (isset($contentJ['reels'])) {
                        error_log("Reels keys: " . implode(', ', array_keys($contentJ['reels'])));
                    }
                }

                // Try different possible response structures
                $highlightData = null;

                // Standard reels_media response
                if (!empty($contentJ['reels'])) {
                    foreach ($contentJ['reels'] as $key => $reel) {
                        if (!empty($reel['items'])) {
                            $highlightData = $reel;
                            break;
                        }
                    }
                }

                // GraphQL response
                if (!$highlightData && !empty($contentJ['data']['reels_media'])) {
                    foreach ($contentJ['data']['reels_media'] as $reel) {
                        if (!empty($reel['items'])) {
                            $highlightData = $reel;
                            break;
                        }
                    }
                }

                // Direct items response
                if (!$highlightData && !empty($contentJ['items'])) {
                    $highlightData = $contentJ;
                }

                if ($highlightData) {
                    $items = $highlightData['items'] ?? [];
                    $ocrLangs = envs('OCR_LANGS','heb+eng');
                    $ffmpeg = envs('FFMPEG_PATH','');

                    if ($debugOn) {
                        error_log("Found " . count($items) . " items in highlight {$highlightTitle}");
                    }

                    foreach ($items as $it) {
                        // Extract full story data for each item in highlight
                        $obj = extractStoryData($it, $uid, $client, $ocrLangs, $ffmpeg, $debugOn);

                        // Add highlight metadata
                        $obj['type'] = 'highlight';
                        $obj['highlight_id'] = $highlightId;
                        $obj['highlight_title'] = $highlightTitle;
                        $obj['highlight_cover_url'] = $highlight['cover_media']['cropped_image_version']['url'] ?? null;

                        $out[] = $obj;
                    }
                } else {
                    if ($debugOn) {
                        error_log("No highlight data found for {$highlightId}");
                        error_log("Response structure: " . substr(json_encode($contentJ), 0, 500));
                    }
                }
            }
        } else {
            if ($debugOn) {
                error_log("Failed to fetch highlight {$highlightId}: HTTP {$contentCode}");
                error_log(substr($contentBody, 0, 200));
            }
        }

        // Add a small delay to avoid rate limiting
        usleep(500000); // 0.5 second delay between highlights
    }

} else {
    /* ---------- Get specific highlight content ---------- */
    $res = $client->get("api/v1/feed/reels_media/?reel_ids=highlight%3A{$highlightId}");
    $code = $res->getStatusCode();
    $body = (string)$res->getBody();

    if ($code !== 200) jdie("HTTP {$code} response:\n{$body}", 1);

    $j = json_decode($body, true);
    if (!is_array($j)) jdie("Bad JSON\n{$body}", 1);

    if ($debugOn) {
        @file_put_contents(__DIR__.'/highlights_debug.json', json_encode($j, JSON_UNESCAPED_UNICODE|JSON_PRETTY_PRINT));
    }

    // Extract highlight media items
    $highlightData = $j['reels']['highlight:'.$highlightId] ?? null;
    if (!$highlightData) jdie("Highlight not found", 1);

    $items = $highlightData['items'] ?? [];
    $ocrLangs = envs('OCR_LANGS','heb+eng');
    $ffmpeg = envs('FFMPEG_PATH','');

    foreach ($items as $it) {
        // Reuse the same extraction logic as stories
        $obj = extractStoryData($it, $uid, $client, $ocrLangs, $ffmpeg, $debugOn);

        // Override type to indicate this is from highlights
        $obj['type'] = 'highlight';
        $obj['highlight_id'] = $highlightId;

        $out[] = $obj;
    }
}

/* ---------- output ---------- */
header('Content-Type: application/json; charset=utf-8');
echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT), PHP_EOL;