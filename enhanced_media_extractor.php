<?php
// enhanced_media_extractor.php
// Usage: php enhanced_media_extractor.php <USER_ID> [--type=story|reel]
// Comprehensive Instagram media analysis with OCR, sticker detection, and advanced features

declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';
require __DIR__ . '/ocr_helper.php';
require __DIR__ . '/brand_detector.php';
use GuzzleHttp\Client;

// Helper functions
function env(string $k, string $d = ''): string {
    $v = getenv($k);
    return $v === false ? $d : $v;
}

function generateContentHash(array $data): string {
    // Create deterministic hash based on media content
    $hashData = [
        'media_id' => $data['media_id'] ?? '',
        'user_id' => $data['user_id'] ?? '',
        'taken_at' => $data['taken_at_iso'] ?? '',
        'image_url' => $data['image_url'] ?? '',
        'video_url' => $data['video_url'] ?? '',
    ];
    return hash('sha256', json_encode($hashData, JSON_UNESCAPED_SLASHES));
}

function detectLanguage(string $text): ?string {
    if (empty(trim($text))) return null;

    // Simple language detection based on character sets and common words
    $hebrewPattern = '/[\x{0590}-\x{05FF}]/u';
    $arabicPattern = '/[\x{0600}-\x{06FF}]/u';
    $englishWords = ['the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can', 'had', 'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his', 'how', 'man', 'new', 'now', 'old', 'see', 'two', 'way', 'who', 'boy', 'did', 'its', 'let', 'put', 'say', 'she', 'too', 'use'];

    if (preg_match($hebrewPattern, $text)) {
        return 'he';
    }
    if (preg_match($arabicPattern, $text)) {
        return 'ar';
    }

    // Check for English words
    $words = preg_split('/\s+/', strtolower($text));
    $englishCount = 0;
    foreach ($words as $word) {
        if (in_array(trim($word, '.,!?'), $englishWords)) {
            $englishCount++;
        }
    }

    if ($englishCount > 0 && $englishCount / count($words) > 0.1) {
        return 'en';
    }

    return null;
}

function performOCR(string $imageUrl, OCRHelper $ocrHelper = null): array {
    if (!$ocrHelper) {
        // Fallback to mock implementation if no OCR helper provided
        return [
            'text' => null,
            'confidence' => 0.0,
            'text_candidates' => [],
            'note' => 'OCR disabled - install Tesseract or set GOOGLE_VISION_API_KEY for real OCR'
        ];
    }

    if (empty($imageUrl)) {
        return [
            'text' => null,
            'confidence' => 0.0,
            'text_candidates' => []
        ];
    }

    try {
        return $ocrHelper->extractText($imageUrl);
    } catch (Exception $e) {
        error_log("OCR failed for $imageUrl: " . $e->getMessage());
        return [
            'text' => null,
            'confidence' => 0.0,
            'text_candidates' => [],
            'error' => $e->getMessage()
        ];
    }
}

function detectStickers(string $imageUrl, array $apiStickers = []): array {
    $stickers = [];

    // Process API-provided stickers first
    foreach ($apiStickers as $type => $stickerData) {
        if (empty($stickerData)) continue;

        switch ($type) {
            case 'mentions':
                foreach ($stickerData as $mention) {
                    $stickers[] = [
                        'type' => 'generic',
                        'text' => '@' . ($mention['username'] ?? $mention),
                        'bbox' => isset($mention['x']) ? [
                            (int)($mention['x'] * 100),
                            (int)($mention['y'] * 100),
                            (int)($mention['w'] * 100),
                            (int)($mention['h'] * 100)
                        ] : null,
                        'confidence' => 0.95
                    ];
                }
                break;

            case 'hashtags':
                foreach ($stickerData as $hashtag) {
                    $stickers[] = [
                        'type' => 'generic',
                        'text' => '#' . ($hashtag['tag'] ?? $hashtag),
                        'bbox' => isset($hashtag['x']) ? [
                            (int)($hashtag['x'] * 100),
                            (int)($hashtag['y'] * 100),
                            50, 20 // Default size
                        ] : null,
                        'confidence' => 0.90
                    ];
                }
                break;

            case 'links':
                foreach ($stickerData as $link) {
                    $stickers[] = [
                        'type' => 'url',
                        'text' => $link['url'] ?? $link,
                        'bbox' => null,
                        'confidence' => 0.85
                    ];
                }
                break;

            case 'products':
                foreach ($stickerData as $product) {
                    if (isset($product['price'])) {
                        $stickers[] = [
                            'type' => 'price',
                            'text' => $product['price'],
                            'bbox' => null,
                            'confidence' => 0.88
                        ];
                    }
                }
                break;
        }
    }

    // In a real implementation, you would also:
    // 1. Download and analyze the image
    // 2. Use computer vision to detect visual stickers
    // 3. Extract text from sticker areas
    // 4. Classify sticker types (coupon, date, price, etc.)

    return $stickers;
}

function extractUrls(string $text, array $linkStickers = []): array {
    $urls = [];

    // Extract URLs from text
    if (preg_match_all('/https?:\/\/[^\s\)\]]+/i', $text, $matches)) {
        foreach ($matches[0] as $url) {
            $urls[] = [
                'text' => trim($url),
                'resolved_domain' => parse_url($url, PHP_URL_HOST)
            ];
        }
    }

    // Check for "link in bio" patterns
    if (preg_match('/link\s+in\s+bio/i', $text)) {
        $urls[] = [
            'text' => 'link in bio',
            'resolved_domain' => null
        ];
    }

    // Add sticker links
    foreach ($linkStickers as $link) {
        $linkUrl = $link['url'] ?? $link;
        $urls[] = [
            'text' => $linkUrl,
            'resolved_domain' => parse_url($linkUrl, PHP_URL_HOST)
        ];
    }

    return array_values(array_unique($urls, SORT_REGULAR));
}

function detectBrands(string $imageUrl, string $text, BrandDetector $brandDetector = null): array {
    if (!$brandDetector) {
        // Fallback to simple implementation
        $brands = [];
        $brandKeywords = [
            'nike' => 'Nike', 'adidas' => 'Adidas', 'zara' => 'Zara', 'h&m' => 'H&M',
            'shein' => 'SHEIN', 'amazon' => 'Amazon', 'apple' => 'Apple', 'samsung' => 'Samsung'
        ];

        $textLower = strtolower($text);
        foreach ($brandKeywords as $keyword => $brandName) {
            if (strpos($textLower, $keyword) !== false) {
                $brands[] = [
                    'value' => $brandName,
                    'confidence' => 0.75,
                    'method' => 'text'
                ];
            }
        }
        return $brands;
    }

    try {
        return $brandDetector->detectBrands($imageUrl, $text);
    } catch (Exception $e) {
        error_log("Brand detection failed: " . $e->getMessage());
        return [];
    }
}

function extractVideoFrames(string $videoUrl): array {
    // Mock frame extraction - in real implementation use FFmpeg
    $frames = [];

    if (!empty($videoUrl)) {
        // Simulate frame extraction at different timestamps
        $timestamps = [0, 1500, 3000]; // 0s, 1.5s, 3s in milliseconds
        foreach ($timestamps as $ts) {
            $frames[] = $ts;
        }
    }

    return $frames;
}

// Main execution
$uid = $argv[1] ?? null;
$type = 'story'; // default

// Parse arguments
foreach ($argv as $arg) {
    if (strpos($arg, '--type=') === 0) {
        $type = substr($arg, 7);
    }
}

if (!$uid) {
    fwrite(STDERR, "Usage: php enhanced_media_extractor.php <USER_ID> [--type=story|reel]\n");
    exit(2);
}

// Environment setup
$ua = env('IG_UA', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36');
$csrf = env('IG_CSRF');
$sess = env('IG_SESSIONID');
$dsid = env('IG_DS_USER_ID');

if (!$csrf || !$sess || !$dsid) {
    fwrite(STDERR, "Missing env: IG_CSRF / IG_SESSIONID / IG_DS_USER_ID\n");
    exit(3);
}

// Initialize helper classes
$ocrHelper = null;
$brandDetector = null;

// Try to initialize OCR helper
try {
    $tesseractPath = env('TESSERACT_PATH', 'tesseract');
    $googleApiKey = env('GOOGLE_VISION_API_KEY');

    if ($googleApiKey || is_executable($tesseractPath) || shell_exec("where tesseract 2>nul") || shell_exec("which tesseract 2>/dev/null")) {
        $ocrHelper = new OCRHelper($tesseractPath, sys_get_temp_dir(), $googleApiKey);
        error_log("OCR enabled");
    } else {
        error_log("OCR disabled - Tesseract not found and no Google Vision API key");
    }
} catch (Exception $e) {
    error_log("Failed to initialize OCR: " . $e->getMessage());
}

// Initialize brand detector
try {
    $brandDetector = new BrandDetector();
    error_log("Brand detection enabled");
} catch (Exception $e) {
    error_log("Failed to initialize brand detector: " . $e->getMessage());
}

// HTTP Client setup
$client = new Client([
    'base_uri' => 'https://www.instagram.com/',
    'headers' => [
        'User-Agent' => $ua,
        'X-Requested-With' => 'XMLHttpRequest',
        'X-CSRFToken' => $csrf,
        'Referer' => 'https://www.instagram.com/',
        'Accept' => 'application/json',
        'Cookie' => "csrftoken={$csrf}; sessionid={$sess}; ds_user_id={$dsid};",
    ],
    'http_errors' => false,
    'timeout' => 30,
]);

// API endpoint based on type
$endpoint = $type === 'reel' ? 'api/v1/feed/user/' . $uid . '/story/' : 'api/v1/feed/reels_media/';
$query = $type === 'reel' ? [] : ['reel_ids' => $uid];

$res = $client->get($endpoint, ['query' => $query]);
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

// Extract items based on response structure
$items = [];
if ($type === 'story') {
    $items = $j['reels'][(string)$uid]['items'] ??
             $j['reels_media'][0]['items'] ??
             $j['items'] ?? [];
} else {
    $items = $j['items'] ?? [];
}

$output = [];

foreach ($items as $item) {
    // Basic media info
    $mediaId = $item['id'] ?? '';
    $userId = $item['user']['id'] ?? $uid;
    $username = $item['user']['username'] ?? null;
    $takenAt = $item['taken_at'] ?? null;
    $takenAtIso = $takenAt ? date('c', is_numeric($takenAt) ? (int)$takenAt : strtotime((string)$takenAt)) : null;

    // Media type and URLs
    $hasVideo = !empty($item['video_versions']) || !empty($item['video_url']);
    $mediaType = $type === 'story' ? ($hasVideo ? 'story' : 'story') : 'reel';

    $imageUrl = $item['image_versions2']['candidates'][0]['url'] ??
                $item['display_url'] ??
                $item['image_url'] ?? null;

    $videoUrl = $item['video_versions'][0]['url'] ??
                $item['video_url'] ?? null;

    // Caption text
    $captionText = $item['caption']['text'] ?? $item['caption'] ?? null;

    // Media metadata
    $width = $item['original_width'] ?? 0;
    $height = $item['original_height'] ?? 0;
    $durationMs = isset($item['video_duration']) ? (int)round((float)$item['video_duration'] * 1000) : 0;

    // Extract basic stickers from API
    $apiStickers = [
        'mentions' => [],
        'hashtags' => [],
        'links' => [],
        'products' => []
    ];

    // Mentions
    foreach (($item['reel_mentions'] ?? []) as $m) {
        if ($username = $m['user']['username'] ?? null) {
            $apiStickers['mentions'][] = [
                'username' => $username,
                'x' => $m['x'] ?? null,
                'y' => $m['y'] ?? null,
                'w' => $m['width'] ?? null,
                'h' => $m['height'] ?? null
            ];
        }
    }

    // Hashtags
    foreach (($item['story_hashtags'] ?? []) as $h) {
        if ($tag = $h['hashtag']['name'] ?? null) {
            $apiStickers['hashtags'][] = [
                'tag' => $tag,
                'x' => $h['x'] ?? null,
                'y' => $h['y'] ?? null
            ];
        }
    }

    // Links
    foreach (($item['story_cta'] ?? []) as $cta) {
        foreach (($cta['links'] ?? []) as $lnk) {
            if ($url = $lnk['webUri'] ?? $lnk['url'] ?? null) {
                $apiStickers['links'][] = ['url' => $url];
            }
        }
    }

    // Products
    foreach (($item['story_product_items'] ?? []) as $p) {
        $prod = $p['product_item'] ?? [];
        $apiStickers['products'][] = [
            'name' => $prod['title'] ?? $prod['name'] ?? '',
            'price' => $prod['current_price'] ?? null
        ];
    }

    // Perform OCR analysis
    $ocrResult = performOCR($imageUrl ?? '', $ocrHelper);
    $ocrText = $ocrResult['text'];
    $ocrConfidence = $ocrResult['confidence'];

    // Combine all text sources
    $allText = implode(' ', array_filter([
        $captionText,
        $ocrText,
        implode(' ', array_column($apiStickers['mentions'], 'username')),
        implode(' ', array_column($apiStickers['hashtags'], 'tag'))
    ]));

    // Advanced analysis - enhance stickers with OCR detection
    $detectedStickers = detectStickers($imageUrl ?? '', $apiStickers);
    if ($ocrHelper && !empty($ocrText)) {
        $ocrStickers = $ocrHelper->detectStickerTypes($ocrText);
        $detectedStickers = array_merge($detectedStickers, $ocrStickers);
    }

    $extractedUrls = extractUrls($allText, $apiStickers['links']);
    $brandCandidates = detectBrands($imageUrl ?? '', $allText, $brandDetector);
    $languageGuess = detectLanguage($allText);

    // Extract hashtags and mentions from text
    $hashtags = [];
    $mentions = [];

    if (preg_match_all('/#([\p{L}\p{N}_]+)/u', $allText, $hashMatches)) {
        $hashtags = array_merge($hashtags, $hashMatches[1]);
    }
    foreach ($apiStickers['hashtags'] as $h) {
        $hashtags[] = $h['tag'];
    }
    $hashtags = array_unique($hashtags);

    if (preg_match_all('/@([\p{L}\p{N}_\.]+)/u', $allText, $mentionMatches)) {
        $mentions = array_merge($mentions, $mentionMatches[1]);
    }
    foreach ($apiStickers['mentions'] as $m) {
        $mentions[] = $m['username'];
    }
    $mentions = array_unique($mentions);

    // Video frame analysis
    $framesUsed = $hasVideo ? extractVideoFrames($videoUrl ?? '') : [];

    // Build final output structure
    $mediaData = [
        'media_id' => $mediaId,
        'user_id' => (string)$userId,
        'username' => $username,
        'type' => $mediaType,
        'taken_at_iso' => $takenAtIso,

        'permalink' => null, // Would need separate API call to get permalink
        'image_url' => $imageUrl,
        'video_url' => $videoUrl,

        'caption_text' => $captionText,
        'ocr_text' => $ocrText,
        'ocr_confidence' => $ocrConfidence,

        'stickers' => $detectedStickers,
        'urls' => $extractedUrls,

        'raw_text_candidates' => $ocrResult['text_candidates'],
        'hashtags' => array_values($hashtags),
        'mentions' => array_values($mentions),

        'frames_used' => $framesUsed,
        'media_meta' => [
            'width' => $width,
            'height' => $height,
            'duration_ms' => $durationMs
        ],

        'language_guess' => $languageGuess,
        'brand_candidates' => $brandCandidates,

        'source_flags' => [
            'has_text' => !empty($allText),
            'has_stickers' => !empty($detectedStickers),
            'has_logo_hint' => !empty(array_filter($brandCandidates, fn($b) => $b['method'] === 'logo'))
        ],

        'content_hash' => '', // Will be set below
        'processing' => [
            'extraction_version' => '1.0.0',
            'errors' => []
        ]
    ];

    // Generate content hash
    $mediaData['content_hash'] = generateContentHash($mediaData);

    $output[] = $mediaData;
}

// Output final JSON
header('Content-Type: application/json; charset=utf-8');
echo json_encode($output, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT);