<?php
declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use GuzzleHttp\Client;
use GuzzleHttp\Cookie\FileCookieJar;
use GuzzleHttp\Cookie\SetCookie;
use GuzzleHttp\Exception\RequestException;
use InstagramScraper\Instagram;
use Phpfastcache\Helper\Psr16Adapter;
use Phpfastcache\Drivers\Files\Config as FilesConfig;

function mask(string $s): string {
    if ($s === '') return '(empty)';
    return substr($s, 0, 2) . str_repeat('*', max(0, strlen($s) - 4)) . substr($s, -2);
}
function printHeader(string $title) { echo "\n==================== {$title} ====================\n"; }

$IG_USER       = getenv('IG_USER')       ?: '';
$IG_PASS       = getenv('IG_PASS')       ?: '';
$IG_SESSIONID  = getenv('IG_SESSIONID')  ?: '';
$IG_DS_USER_ID = getenv('IG_DS_USER_ID') ?: '';
$IG_CSRF       = getenv('IG_CSRF')       ?: '';
$IG_EXACT_UA   = getenv('IG_EXACT_UA')   ?: ''; // <<< UA המדויק מהדפדפן
$TARGET        = $argv[1] ?? 'instagram';
$forceLogin    = isset($argv[2]) && in_array(strtolower($argv[2]), ['1','true','force','yes','y'], true);

printHeader('ENV CHECK');
echo "IG_USER      = " . ($IG_USER === '' ? '(missing)' : $IG_USER) . PHP_EOL;
echo "IG_PASS      = " . ($IG_PASS === '' ? '(missing)' : 'len='.strlen($IG_PASS).', mask='.mask($IG_PASS)) . PHP_EOL;
echo "IG_SESSIONID = " . ($IG_SESSIONID === '' ? '(missing)' : mask($IG_SESSIONID)) . PHP_EOL;
echo "IG_DS_USER_ID= " . ($IG_DS_USER_ID === '' ? '(missing)' : $IG_DS_USER_ID) . PHP_EOL;
echo "IG_CSRF      = " . ($IG_CSRF === '' ? '(missing)' : mask($IG_CSRF)) . PHP_EOL;
echo "IG_EXACT_UA  = " . ($IG_EXACT_UA === '' ? '(missing)' : $IG_EXACT_UA) . PHP_EOL;

$baseDir   = __DIR__;
$cookieDir = $baseDir . '/cookies';
$cacheDir  = $baseDir . '/cache';
if (!is_dir($cookieDir)) mkdir($cookieDir, 0777, true);
if (!is_dir($cacheDir))  mkdir($cacheDir, 0777, true);

$cookieFile = $cookieDir . '/ig_cookies.json';
$jar = new FileCookieJar($cookieFile, true);

// === יצירת קליינט עם UA המדויק ===
$client = new Client([
    'headers' => [
        'User-Agent'      => $IG_EXACT_UA !== '' ? $IG_EXACT_UA : 'Mozilla/5.0', // חובה להעביר UA זהה לדפדפן שממנו הוצאת sessionid
        'Accept'          => '*/*',
        'Accept-Language' => 'en-US,en;q=0.9,he;q=0.8',
        'Referer'         => 'https://www.instagram.com/',
        'Origin'          => 'https://www.instagram.com',
    ],
    'cookies' => $jar,
    'timeout' => 35,
]);

// === הזרקת קוקיז מה-ENV (אם קיימים) לפני כל הבקשות ===
if ($IG_SESSIONID !== '') {
    foreach ([
        ['name' => 'sessionid',  'value' => $IG_SESSIONID],
        ['name' => 'ds_user_id', 'value' => $IG_DS_USER_ID],
        ['name' => 'csrftoken',  'value' => $IG_CSRF],
    ] as $c) {
        if ($c['value'] === '') continue;
        $jar->setCookie(new SetCookie([
            'Name'     => $c['name'],
            'Value'    => $c['value'],
            'Domain'   => '.instagram.com',
            'Path'     => '/',
            'Secure'   => true,
            'HttpOnly' => true,
        ]));
    }
}

// === פונקציה שמחזירה headers של אינסטה-ווב לכל בקשה ===
$igWwwClaim = null;
function igHeaders(): array {
    global $IG_EXACT_UA, $IG_CSRF, $igWwwClaim;
    $h = [
        'User-Agent'  => $IG_EXACT_UA,
        'X-IG-App-ID' => '936619743392459',   // מזהה web
        'Accept'      => '*/*',
        'Referer'     => 'https://www.instagram.com/',
        'Origin'      => 'https://www.instagram.com',
    ];
    if ($IG_CSRF)      $h['X-CSRFToken']    = $IG_CSRF;
    if ($igWwwClaim)   $h['X-IG-WWW-Claim'] = $igWwwClaim;
    return $h;
}

// === בקשת חימום כדי לקבל mid/ig_did וגם ללכוד X-IG-Set-WWW-Claim אם נשלח ===
printHeader('WARMUP: GET /');
$warm = $client->request('GET', 'https://www.instagram.com/', ['http_errors' => false, 'debug' => fopen('php://stderr','w')]);
$claim = $warm->getHeaderLine('X-IG-Set-WWW-Claim');
if ($claim) {
    $igWwwClaim = $claim;
    echo "Captured X-IG-Set-WWW-Claim: {$igWwwClaim}\n";
}

function doReq(Client $client, string $url, array $opt = []): array {
    $opt['http_errors'] = false;
    $opt['debug'] = fopen('php://stderr', 'w');
    // הזרקת כותרות אינסטה לכל בקשה
    $opt['headers'] = isset($opt['headers']) ? array_merge(igHeaders(), $opt['headers']) : igHeaders();
    try {
        $res = $client->request('GET', $url, $opt);
        return [
            'ok'     => true,
            'code'   => $res->getStatusCode(),
            'reason' => $res->getReasonPhrase(),
            'headers'=> $res->getHeaders(),
            'body'   => substr((string)$res->getBody(), 0, 2000),
        ];
    } catch (RequestException $e) {
        $r = $e->getResponse();
        return [
            'ok'     => false,
            'code'   => $r ? $r->getStatusCode() : null,
            'reason' => $r ? $r->getReasonPhrase() : $e->getMessage(),
            'headers'=> $r ? $r->getHeaders() : [],
            'body'   => $r ? substr((string)$r->getBody(), 0, 2000) : '',
            'error'  => get_class($e) . ': ' . $e->getMessage(),
        ];
    } catch (\Throwable $e) {
        return ['ok'=>false,'error'=>get_class($e).': '.$e->getMessage()];
    }
}

// === בדיקות דיאגנוסטיקה ישירות ===
printHeader('REQ #1: GET / (homepage)');
$r1 = doReq($client, 'https://www.instagram.com/');
echo json_encode(['status'=>$r1['code'],'reason'=>$r1['reason'],'location'=>$r1['headers']['Location'][0] ?? null], JSON_PRETTY_PRINT|JSON_UNESCAPED_UNICODE).PHP_EOL;

printHeader('REQ #2: GET /api/v1/accounts/current_user/');
$r2 = doReq($client, 'https://www.instagram.com/api/v1/accounts/current_user/');
echo json_encode([
    'status'=>$r2['code'],
    'reason'=>$r2['reason'],
    'location'=>$r2['headers']['Location'][0] ?? null,
    'snippet'=>substr($r2['body'], 0, 200)
], JSON_PRETTY_PRINT|JSON_UNESCAPED_UNICODE).PHP_EOL;

printHeader('REQ #3: GET /api/v1/users/web_profile_info/?username=' . $TARGET);
$r3 = doReq($client, 'https://www.instagram.com/api/v1/users/web_profile_info/?username=' . rawurlencode($TARGET));
echo json_encode([
    'status'=>$r3['code'],
    'reason'=>$r3['reason'],
    'location'=>$r3['headers']['Location'][0] ?? null,
    'snippet'=>substr($r3['body'], 0, 200)
], JSON_PRETTY_PRINT|JSON_UNESCAPED_UNICODE).PHP_EOL;

// === צילום מצב קובץ קוקיז ===
printHeader('COOKIE FILE SNAPSHOT');
if (file_exists($cookieFile)) {
    $cookieJson = file_get_contents($cookieFile);
    echo substr($cookieJson, 0, 2000) . (strlen($cookieJson) > 2000 ? "\n...[truncated]..." : '') . PHP_EOL;
} else {
    echo "cookie file not found: {$cookieFile}\n";
}

// === ניסיון דרך InstagramScraper ===
printHeader('InstagramScraper attempt (getAccount)');
$cache = new Psr16Adapter('Files', new FilesConfig(['path' => $cacheDir]));
$ig = Instagram::withCredentials($client, $IG_USER, $IG_PASS, $cache);

try {
    if ($IG_SESSIONID === '') {
        $ig->login($forceLogin);
        $ig->saveSession();
        echo "login(): OK\n";
    } else {
        echo "Skipping login() — using session cookies.\n";
    }

    $acc = $ig->getAccount($TARGET);
    echo json_encode([
        'ok' => true,
        'username' => $acc->getUsername(),
        'followers'=> $acc->getFollowedByCount(),
    ], JSON_PRETTY_PRINT|JSON_UNESCAPED_UNICODE) . PHP_EOL;

} catch (\Throwable $e) {
    $err = ['msg' => $e->getMessage(), 'class' => get_class($e)];
    if (method_exists($e, 'getResponse') && $e->getResponse()) {
        $resp = $e->getResponse();
        $err['http_code']  = $resp->getStatusCode();
        $err['http_body']  = substr((string)$resp->getBody(), 0, 1000);
        $err['http_headers'] = $resp->getHeaders();
    }
    echo json_encode(['ok'=>false,'error'=>$err], JSON_PRETTY_PRINT|JSON_UNESCAPED_UNICODE) . PHP_EOL;
}
