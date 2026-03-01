import os
import httpx
import time

# 需要下载的 5 个前端核心库 (使用国内七牛云极速稳定节点)
urls = {
    "index.min.css": "https://cdn.staticfile.net/element-plus/2.3.8/index.min.css",
    "vue.global.prod.min.js": "https://cdn.staticfile.net/vue/3.3.4/vue.global.prod.min.js",
    "index.full.min.js": "https://cdn.staticfile.net/element-plus/2.3.8/index.full.min.js",
    "axios.min.js": "https://cdn.staticfile.net/axios/1.4.0/axios.min.js",
    "index.iife.min.js": "https://cdn.staticfile.net/element-plus-icons-vue/2.1.0/index.iife.min.js"
}

os.makedirs("static/lib", exist_ok=True)
print("🚀 开始使用【高容错并发模式】下载前端核心库...")

with httpx.Client(timeout=30.0, follow_redirects=True) as client:
    for name, url in urls.items():
        file_path = f"static/lib/{name}"
        success = False
        for attempt in range(1, 4):
            print(f"正在下载: {name} (尝试 {attempt}/3)...", end=" ", flush=True)
            try:
                response = client.get(url)
                response.raise_for_status()
                # 校验文件大小，防止下载了0字节的空文件
                if len(response.content) < 1000:
                    raise Exception("文件内容异常，过小")
                with open(file_path, "wb") as f:
                    f.write(response.content)
                print("✅ 成功")
                success = True
                break
            except Exception as e:
                print(f"❌ 失败 ({type(e).__name__})")
                time.sleep(1.5)
        if not success:
            print(f"⚠️ 警告: {name} 下载失败，请检查网络！")

print("\n🎉 下载流程结束！请确认上方5个文件全部显示 ✅ 成功。")