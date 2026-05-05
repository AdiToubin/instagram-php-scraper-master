<?php
// get_user_ids.php
// Usage: php get_user_ids.php username1 username2 username3 ...
// Or: php get_user_ids.php --file usernames.txt
// Env required: IG_SESSIONID, IG_CSRF, IG_DS_USER_ID
// Optional: IG_UA
declare(strict_types=1);

require __DIR__ . '/vendor/autoload.php';

use GuzzleHttp\Client;
use GuzzleHttp\Handler\CurlHandler;
use GuzzleHttp\HandlerStack;

/* ---------- helpers ---------- */
function envs(string $k, ?string $def=''): string { 
    $v=getenv($k); 
    return ($v===false)?(string)$def:(string)$v; 
}

function jdie(string $m, int $c=1): void { 
    fwrite(STDERR, $m.PHP_EOL); 
    exit($c); 
}

/* ---------- inputs/env ---------- */
$usernames = [];

// Check if --file flag is used
if (isset($argv[1]) && $argv[1] === '--file') {
    if (!isset($argv[2])) {
        jdie("Usage: php get_user_ids.php --file <filename>\n   or: php get_user_ids.php username1 username2 ...", 2);
    }
    
    $filename = $argv[2];
    if (!file_exists($filename)) {
        jdie("File not found: {$filename}", 3);
    }
    
    $lines = file($filename, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    if ($lines === false) {
        jdie("Could not read file: {$filename}", 3);
    }
    
    foreach ($lines as $line) {
        $line = trim($line);
        // Skip comments and empty lines
        if ($line === '' || $line[0] === '#') continue;
        $usernames[] = $line;
    }
} else {
    // Get usernames from command line arguments
    if (count($argv) < 2) {
        jdie("Usage: php get_user_ids.php username1 username2 ...\n   or: php get_user_ids.php --file usernames.txt", 2);
    }
    
    $usernames = array_slice($argv, 1);
}

if (empty($usernames)) {
    jdie("No usernames provided", 2);
}

// Get Instagram credentials from environment
$csrf = envs('IG_CSRF', null);
$sess = envs('IG_SESSIONID', null);
$dsid = envs('IG_DS_USER_ID', null);
$ua   = envs('IG_UA', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36');

if (!$csrf || !$sess || !$dsid) {
    jdie("Missing env: IG_CSRF / IG_SESSIONID / IG_DS_USER_ID", 3);
}

/* ---------- setup HTTP client ---------- */
$handler = HandlerStack::create(new CurlHandler());
$verifyPath = ini_get('curl.cainfo');
if (!$verifyPath) $verifyPath = ini_get('openssl.cafile') ?: true;

$client = new Client([
    'base_uri' => 'https://www.instagram.com/',
    'handler' => $handler,
    'http_errors' => false,
    'timeout' => 30,
    'decode_content' => true,
    'verify' => $verifyPath,
    'force_ip_resolve' => 'v4',
    'curl' => [CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_1_1],
    'headers' => [
        'User-Agent' => $ua,
        'Referer' => 'https://www.instagram.com/',
        'Origin' => 'https://www.instagram.com',
        'Accept' => '*/*',
        'Accept-Language' => 'en-US,en;q=0.9',
        'Accept-Encoding' => 'gzip, deflate',
        'X-Requested-With' => 'XMLHttpRequest',
        'X-IG-App-ID' => '936619743392459',
        'X-CSRFToken' => $csrf,
        'Cookie' => "csrftoken={$csrf}; sessionid={$sess}; ds_user_id={$dsid};",
        'Sec-Fetch-Site' => 'same-origin',
        'Sec-Fetch-Mode' => 'cors',
        'Sec-Fetch-Dest' => 'empty',
    ],
]);

/* ---------- fetch user IDs ---------- */
$results = [];
$errors = [];

echo "Fetching user IDs for " . count($usernames) . " username(s)...\n\n";

foreach ($usernames as $username) {
    $username = trim($username);
    if ($username === '') continue;
    
    echo "Processing: @{$username} ... ";
    
    try {
        // Use Instagram's web API to get user info
        $res = $client->get("api/v1/users/web_profile_info/?username=" . urlencode($username));
        $code = $res->getStatusCode();
        $body = (string)$res->getBody();
        
        if ($code !== 200) {
            echo "ERROR (HTTP {$code})\n";
            $errors[] = [
                'username' => $username,
                'error' => "HTTP {$code}",
                'body' => substr($body, 0, 200)
            ];
            continue;
        }
        
        $data = json_decode($body, true);
        
        if (!is_array($data)) {
            echo "ERROR (Invalid JSON)\n";
            $errors[] = [
                'username' => $username,
                'error' => 'Invalid JSON response'
            ];
            continue;
        }
        
        // Extract user ID from response
        $userId = $data['data']['user']['id'] ?? null;
        
        if (!$userId) {
            echo "ERROR (User ID not found)\n";
            $errors[] = [
                'username' => $username,
                'error' => 'User ID not found in response'
            ];
            continue;
        }
        
        $fullName = $data['data']['user']['full_name'] ?? '';
        $isPrivate = $data['data']['user']['is_private'] ?? false;
        $followerCount = $data['data']['user']['edge_followed_by']['count'] ?? 0;
        
        $results[] = [
            'username' => $username,
            'user_id' => $userId,
            'full_name' => $fullName,
            'is_private' => $isPrivate,
            'followers' => $followerCount
        ];
        
        echo "✓ ID: {$userId}\n";
        
        // Small delay to avoid rate limiting
        usleep(500000); // 0.5 seconds
        
    } catch (\Throwable $e) {
        echo "ERROR (" . $e->getMessage() . ")\n";
        $errors[] = [
            'username' => $username,
            'error' => $e->getMessage()
        ];
    }
}

/* ---------- output results ---------- */
echo "\n" . str_repeat("=", 60) . "\n";
echo "RESULTS\n";
echo str_repeat("=", 60) . "\n\n";

if (!empty($results)) {
    echo "Successfully fetched " . count($results) . " user ID(s):\n\n";
    
    // Table format
    printf("%-20s %-15s %-30s %s\n", "Username", "User ID", "Full Name", "Followers");
    echo str_repeat("-", 100) . "\n";
    
    foreach ($results as $r) {
        printf(
            "%-20s %-15s %-30s %s%s\n",
            "@" . $r['username'],
            $r['user_id'],
            mb_substr($r['full_name'], 0, 28),
            number_format($r['followers']),
            $r['is_private'] ? ' 🔒' : ''
        );
    }
    
    echo "\n" . str_repeat("-", 60) . "\n";
    echo "User IDs only (for batch processing):\n";
    echo implode("\n", array_column($results, 'user_id')) . "\n";
    
    echo "\n" . str_repeat("-", 60) . "\n";
    echo "Command to run stories_with_stickers.php for each user:\n\n";
    
    foreach ($results as $r) {
        echo "php stories_with_stickers.php {$r['user_id']}  # @{$r['username']}\n";
    }
    
    // Save results to JSON file
    $outputFile = __DIR__ . '/user_ids_' . date('Y-m-d_His') . '.json';
    file_put_contents($outputFile, json_encode([
        'timestamp' => date('c'),
        'total_requested' => count($usernames),
        'total_found' => count($results),
        'total_errors' => count($errors),
        'results' => $results,
        'errors' => $errors
    ], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));
    
    echo "\n✓ Results saved to: {$outputFile}\n";
}

if (!empty($errors)) {
    echo "\n" . str_repeat("=", 60) . "\n";
    echo "ERRORS (" . count($errors) . ")\n";
    echo str_repeat("=", 60) . "\n\n";
    
    foreach ($errors as $err) {
        echo "• @{$err['username']}: {$err['error']}\n";
    }
}

echo "\n";
