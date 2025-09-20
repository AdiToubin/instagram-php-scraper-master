<?php
declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use GuzzleHttp\Client;
use InstagramScraper\Instagram;
use Phpfastcache\Helper\Psr16Adapter;
use Phpfastcache\Drivers\Files\Config as FilesConfig;

function envOrFail(string $key): string {
    $v = getenv($key);
    if ($v === false || $v === '') {
        fwrite(STDERR, "Missing env $key\n");
        exit(1);
    }
    return $v;
}

/* ===== ENV ===== */
$IG_USER = envOrFail('IG_USER');
$IG_PASS = envOrFail('IG_PASS');
$forceLogin = isset($argv[2]) && in_array(strtolower($argv[2]), ['1','true','force','yes','y'], true);

/* ===== Paths ===== */
$cachePath = __DIR__ . '/cache';
$cookieDir = __DIR__ . '/cookies';
if (!is_dir($cachePath))  mkdir($cachePath, 0777, true);
if (!is_dir($cookieDir))  mkdir($cookieDir, 0777, true);

/* ===== PSR-16 cache (phpfastcache v9) ===== */
$cache = new Psr16Adapter('Files', new FilesConfig([
    'path' => $cachePath,
]));

/* ===== HTTP Client ===== */
$client = new Client([
    'headers' => [
        'User-Agent'      => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                           . 'AppleWebKit/537.36 (KHTML, like Gecko) '
                           . 'Chrome/120.0.0.0 Safari/537.36',
        'Accept'          => 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language' => 'en-US,en;q=0.9,he;q=0.8',
        'Referer'         => 'https://www.instagram.com/',
        'Origin'          => 'https://www.instagram.com',
    ],
    'cookies' => true,
    'timeout' => 35,
    // אם צריך לעבוד דרך פרוקסי רזידנטי:
    // 'proxy' => 'http://user:pass@host:port',
]);

/* ===== Preflight: בקשת חימום לעמוד הראשי כדי לקבל csrftoken ===== */
try {
    $client->request('GET', 'https://www.instagram.com/', [
        'http_errors' => false, // לא לזרוק חריגה על 4xx
    ]);
    usleep(300000); // 300ms
} catch (\Throwable $e) {
    // נתעלם; נמשיך לנסות Login
}

/* ===== Instagram instance ===== */
$ig = Instagram::withCredentials($client, $IG_USER, $IG_PASS, $cache);

try {
    $ig->login($forceLogin);
    $ig->saveSession();
} catch (\Throwable $e) {
    echo json_encode([
        'debug' => [
            'logged_in'   => false,
            'login_error' => $e->getMessage(),
            'source'      => 'login',
        ],
    ], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT) . PHP_EOL;
    exit(1);
}

try {
    $username = $argv[1] ?? 'instagram';
    $account  = $ig->getAccount($username);

    $out = [
        'debug' => [
            'logged_in' => true,
            'source'    => 'web_profile_info',
        ],
        'id'              => $account->getId(),
        'username'        => $account->getUsername(),
        'full_name'       => $account->getFullName(),
        'biography'       => $account->getBiography(),
        'followers'       => $account->getFollowedByCount(),
        'following'       => $account->getFollowsCount(),
        'is_verified'     => $account->isVerified(),
        'profile_pic_url' => $account->getProfilePicUrl(),
    ];

    /* ===== Stories ===== */
    $storiesOut = [];
    try {
        $userId = $account->getId();
        $storiesContainers = null;
        if (method_exists($ig, 'getStoriesByUserId')) {
            $storiesContainers = $ig->getStoriesByUserId($userId);
        } elseif (method_exists($ig, 'getStories')) {
            $storiesContainers = $ig->getStories($userId);
        }
        if (is_array($storiesContainers)) {
            foreach ($storiesContainers as $container) {
                $items = is_object($container) && method_exists($container, 'getItems')
                    ? $container->getItems()
                    : (is_array($container) ? $container : []);
                foreach ($items as $item) {
                    $storiesOut[] = [
                        'taken_at'  => method_exists($item,'getCreatedTime') ? $item->getCreatedTime() : null,
                        'is_video'  => method_exists($item,'isVideo') ? $item->isVideo() : null,
                        'link'      => method_exists($item,'getLink') ? $item->getLink() : null,
                        'img_url'   => method_exists($item,'getImageHighResolutionUrl') ? $item->getImageHighResolutionUrl() : null,
                        'video_url' => method_exists($item,'getVideoUrl') ? $item->getVideoUrl() : null,
                    ];
                }
            }
        }
    } catch (\Throwable $e) {
        $storiesOut = [];
    }
    $out['stories'] = $storiesOut;

    /* ===== Reels (מסוננים ממדיות) ===== */
    $reelsOut = [];
    try {
        $medias = $ig->getMedias($username, 24);
        foreach ($medias as $m) {
            $type        = method_exists($m,'getType') ? $m->getType() : null;
            $productType = method_exists($m,'getProductType') ? $m->getProductType() : null; // 'clips' = Reels
            $isReel = ($productType === 'clips') || (strtolower((string)$type) === 'video');
            if ($isReel) {
                $reelsOut[] = [
                    'shortcode' => method_exists($m,'getShortCode') ? $m->getShortCode() : null,
                    'caption'   => method_exists($m,'getCaption') ? $m->getCaption() : null,
                    'link'      => method_exists($m,'getLink') ? $m->getLink() : null,
                    'thumb'     => method_exists($m,'getImageHighResolutionUrl') ? $m->getImageHighResolutionUrl() : null,
                    'video_url' => method_exists($m,'getVideoUrl') ? $m->getVideoUrl() : null,
                    'taken_at'  => method_exists($m,'getCreatedTime') ? $m->getCreatedTime() : null,
                    'product'   => $productType,
                    'type'      => $type,
                ];
            }
        }
    } catch (\Throwable $e) {
        $reelsOut = [];
    }
    $out['reels'] = $reelsOut;

    echo json_encode($out, JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT) . PHP_EOL;

} catch (\Throwable $e) {
    echo json_encode([
        'debug' => [
            'logged_in'     => true,
            'runtime_error' => $e->getMessage(),
            'source'        => 'runtime',
        ],
    ], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT) . PHP_EOL;
    exit(1);
}
