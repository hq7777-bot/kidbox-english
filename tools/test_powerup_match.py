import sys
sys.path.insert(0, '/opt/xiaozhi-esp32-server')
from plugins_func.functions.play_music import get_music_files, _find_best_match, _parse_powerup

files, names = get_music_files('/opt/xiaozhi-esp32-server/data/lessons', ('.mp3', '.wav'))
print(f'音频总数: {len(files)}  PowerUp: {len([f for f in files if "PowerUp" in f])}')

tests = [
    # POWER UP 自然语言
    '播放 power up 第一单元故事',
    'power up 1 unit 3 song',
    '剑桥一级第2单元歌曲',
    '播放 power up 第一单元文学',
    'POWER UP 第9单元故事',
    # POWER UP 编号
    '播放P1-03',
    '放 p1-14',
    # 回归：Smart Reading 不能被抢走
    '播放3-3第15课课文',
    '播放3-2第七单元单词',
    '3杠三unit15 passenge',
    '播放book三点三15课课文',
    # 其它
    '001',
    'hello',
]
for q in tests:
    print(f'  {q!r:34} pu={_parse_powerup(q)}')
    print(f'       -> {_find_best_match(q, files)}')
