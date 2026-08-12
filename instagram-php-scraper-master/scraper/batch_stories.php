<?php
// batch_stories.php
// Usage: php batch_stories.php user_id1 user_id2 user_id3 ...
// Or: php batch_stories.php --file user_ids.txt
// Runs stories_with_stickers.php for multiple users and combines results
// Env required: Same as stories_with_stickers.php
declare(strict_types=1);

/* ---------- helpers ---------- */
function jdie(string $m, int $c=1): void { 
    fwrite(STDERR, $m.PHP_EOL); 
    exit($c); 
}

/* ---------- inputs ---------- */
$userIds = [];

// Check if --file flag is used
if (isset($argv[1]) && $argv[1] === '--file') {
    if (!isset($argv[2])) {
        jdie("Usage: php batch_stories.php --file <filename>\n   or: php batch_stories.php user_id1 user_id2 ...", 2);
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
        $userIds[] = $line;
    }
} else {
    // Get user IDs from command line arguments
    if (count($argv) < 2) {
        jdie("Usage: php batch_stories.php user_id1 user_id2 ...\n   or: php batch_stories.php --file user_ids.txt", 2);
    }
    
    $userIds = array_slice($argv, 1);
}

if (empty($userIds)) {
    jdie("No user IDs provided", 2);
}

/* ---------- process each user ---------- */
$allStories = [];
$errors = [];
$totalStories = 0;

echo "Processing stories for " . count($userIds) . " user(s)...\n\n";

foreach ($userIds as $userId) {
    $userId = trim($userId);
    if ($userId === '') continue;
    
    echo "Fetching stories for user ID: {$userId} ... ";
    
    // Run stories_with_stickers.php for this user
    $cmd = 'php ' . escapeshellarg(__DIR__ . '/stories_with_stickers.php') . ' ' . escapeshellarg($userId) . ' 2>&1';
    
    $output = [];
    $returnCode = 0;
    exec($cmd, $output, $returnCode);
    
    if ($returnCode !== 0) {
        echo "ERROR (exit code: {$returnCode})\n";
        $errors[] = [
            'user_id' => $userId,
            'error' => 'Script failed with exit code ' . $returnCode,
            'output' => implode("\n", $output)
        ];
        continue;
    }
    
    $jsonOutput = implode("\n", $output);
    $stories = json_decode($jsonOutput, true);
    
    if (!is_array($stories)) {
        echo "ERROR (Invalid JSON)\n";
        $errors[] = [
            'user_id' => $userId,
            'error' => 'Invalid JSON output',
            'output' => implode("\n", $output)
        ];
        continue;
    }
    
    $count = count($stories);
    $totalStories += $count;
    
    echo "✓ Found {$count} story/stories\n";
    
    // Add user_id to each story for tracking
    foreach ($stories as &$story) {
        if (!isset($story['user_id'])) {
            $story['user_id'] = $userId;
        }
    }
    unset($story);
    
    $allStories = array_merge($allStories, $stories);
    
    // Small delay to avoid rate limiting
    sleep(2);
}

/* ---------- output combined results ---------- */
echo "\n" . str_repeat("=", 60) . "\n";
echo "SUMMARY\n";
echo str_repeat("=", 60) . "\n\n";

echo "Total users processed: " . count($userIds) . "\n";
echo "Total stories found: {$totalStories}\n";
echo "Total errors: " . count($errors) . "\n\n";

if (!empty($allStories)) {
    // Group by user
    $byUser = [];
    foreach ($allStories as $story) {
        $uid = $story['user_id'] ?? 'unknown';
        $username = $story['username'] ?? 'unknown';
        
        if (!isset($byUser[$uid])) {
            $byUser[$uid] = [
                'user_id' => $uid,
                'username' => $username,
                'stories' => []
            ];
        }
        
        $byUser[$uid]['stories'][] = $story;
    }
    
    echo "Stories by user:\n";
    foreach ($byUser as $uid => $data) {
        echo "  • @{$data['username']} (ID: {$uid}): " . count($data['stories']) . " story/stories\n";
    }
    
    // Save combined results
    $outputFile = __DIR__ . '/batch_stories_' . date('Y-m-d_His') . '.json';
    file_put_contents($outputFile, json_encode([
        'timestamp' => date('c'),
        'total_users' => count($userIds),
        'total_stories' => $totalStories,
        'total_errors' => count($errors),
        'by_user' => array_values($byUser),
        'all_stories' => $allStories,
        'errors' => $errors
    ], JSON_UNESCAPED_UNICODE | JSON_PRETTY_PRINT));
    
    echo "\n✓ Combined results saved to: {$outputFile}\n";
}

if (!empty($errors)) {
    echo "\n" . str_repeat("=", 60) . "\n";
    echo "ERRORS\n";
    echo str_repeat("=", 60) . "\n\n";
    
    foreach ($errors as $err) {
        echo "• User ID {$err['user_id']}: {$err['error']}\n";
    }
}

echo "\n";
