git branch -a
git checkout main && git reset --hard test1

#将远程端main分支备份到main-20260318-2分支
git fetch origin main && git push origin origin/main:refs/heads/main-20260318-2
#将本地当前ai_board分支强行覆盖到远程端main分支git branch --show-current && git log --oneline -3
git push --force origin ai_board:main
