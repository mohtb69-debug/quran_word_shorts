# كلمة من القرآن
مقطع تلاوة عشوائي من assets/audio (اسمه يحدد الآية) ← Groq يختار كلمة من الآية وجملة شرح **منقولة حرفيًا** من التفسير (Quran.com / alquran.cloud) ← فيديو ← YouTube.
لا يُولَّد أي نص قرآني أو تفسيري بالذكاء الاصطناعي: المقتطف يُرفض إن لم يكن جزءًا حرفيًا من التفسير.

الأسرار: GROQ_API_KEY, YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
المتغيرات (Variables): RECITER (اسم القارئ)، PRIVACY، MADE_FOR_KIDS
فحص الصوت:  python publish.py --check        اختبار:  python publish.py --no-upload --keep
cron-job.org: POST https://api.github.com/repos/USER/REPO/actions/workflows/quran-word.yml/dispatches   {"ref":"main"}
