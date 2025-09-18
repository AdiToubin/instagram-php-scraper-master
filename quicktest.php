<?php
require __DIR__ . '/vendor/autoload.php';

use InstagramScraper\Instagram;
use Phpfastcache\Helper\Psr16Adapter;

$cache = new Psr16Adapter('Files');
$ig = Instagram::withCredentials(null, null, $cache);

$ig->setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) PHP-InstagramScraper/1.0');
$ig->login(false);

$user = $ig->getAccount('instagram');
echo "User: " . $user->getUsername() . " | Followers: " . $user->getFollowersCount() . PHP_EOL;

$medias = $ig->getMedias('instagram', 5);
foreach ($medias as $m) {
    echo $m->getId() . " | " . $m->getLink() . PHP_EOL;
}
