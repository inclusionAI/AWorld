import asyncio
import json

from dotenv import load_dotenv

from aworld.output import WorkSpace, CodeArtifact, ArtifactType

async def main():
    content = """
    ```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>张居正改革辩论摘要</title>
    <link href="https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@400;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <script src="https://html2canvas.hertzen.com/dist/html2canvas.min.js"></script>
    <style>
        :root {
            --primary-color: #2c3e50;
            --secondary-color: #e74c3c;
            --accent-color: #3498db;
            --light-color: #ecf0f1;
            --dark-color: #2c3e50;
            --shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            --border-radius: 8px;
        }

        body {
            font-family: 'Noto Serif SC', serif;
            line-height: 1.6;
            color: var(--dark-color);
            background-color: #f9f9f9;
            margin: 0;
            padding: 20px;
        }

        .container {
            max-width: 1000px;
            margin: 0 auto;
            background-color: white;
            border-radius: var(--border-radius);
            box-shadow: var(--shadow);
            overflow: hidden;
        }

        .header {
            background: linear-gradient(135deg, var(--primary-color), var(--accent-color));
            color: white;
            padding: 20px;
            text-align: center;
        }

        .header h1 {
            margin: 0;
            font-size: 2.2rem;
        }

        .header p {
            margin: 10px 0 0;
            opacity: 0.9;
        }

        .debate-container {
            display: grid;
            grid-template-columns: 1fr;
            gap: 20px;
            padding: 20px;
        }

        .round {
            background-color: white;
            border-radius: var(--border-radius);
            box-shadow: var(--shadow);
            overflow: hidden;
        }

        .round-header {
            background-color: var(--light-color);
            padding: 10px 15px;
            font-weight: bold;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .affirmative {
            border-left: 4px solid var(--accent-color);
        }

        .negative {
            border-left: 4px solid var(--secondary-color);
        }

        .content {
            padding: 15px;
        }

        .summary {
            font-size: 1.1rem;
            margin-bottom: 15px;
            line-height: 1.6;
        }

        .citation {
            background-color: #f8f9fa;
            padding: 10px;
            border-radius: var(--border-radius);
            font-size: 0.9rem;
        }

        .citation a {
            color: var(--accent-color);
            text-decoration: none;
            word-break: break-all;
        }

        .citation a:hover {
            text-decoration: underline;
        }

        .citation-item {
            margin-bottom: 8px;
        }

        .citation-title {
            font-weight: bold;
            display: block;
        }

        .buttons {
            display: flex;
            justify-content: center;
            margin: 20px 0;
            gap: 10px;
        }

        .btn {
            padding: 10px 20px;
            border: none;
            border-radius: var(--border-radius);
            background-color: var(--accent-color);
            color: white;
            cursor: pointer;
            font-size: 1rem;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .btn:hover {
            background-color: #2980b9;
            transform: translateY(-2px);
        }

        .btn-save {
            background-color: var(--secondary-color);
        }

        .btn-save:hover {
            background-color: #c0392b;
        }

        @media (max-width: 768px) {
            .header h1 {
                font-size: 1.8rem;
            }
            
            .summary {
                font-size: 1rem;
            }
        }
    </style>
</head>
<body>
    <div class="container" id="capture">
        <div class="header">
            <h1>张居正改革历史辩论</h1>
            <p>明代政治家张居正的历史评价与改革成效</p>
        </div>

        <div class="debate-container">
            <div class="round">
                <div class="round-header">
                    <span>第一轮辩论</span>
                </div>
                
                <div class="content affirmative">
                    <div class="summary">
                        <strong>正方观点：</strong>张居正的铁腕改革（如一条鞭法）有效增加了财政收入30%，挽救了明朝财政危机，其改革成果被清朝沿用，证明其价值。
                    </div>
                    <div class="citation">
                        <div class="citation-item">
                            <span class="citation-title">引用资料：</span>
                            <a href="https://m.fx361.cc/news/2025/0417/26824468.html" target="_blank">明代张居正"一条鞭法"赋税改革_参考网</a>
                        </div>
                        <div class="citation-item">
                            <a href="https://zhuanlan.zhihu.com/p/686334044" target="_blank">为明朝续命几十年？张居正推出的《一条鞭法》是什么？ - 知乎</a>
                        </div>
                        <div class="citation-item">
                            <a href="https://baike.baidu.com/item/一条鞭法/356874" target="_blank">一条鞭法 - 百度百科</a>
                        </div>
                        <div class="citation-item">
                            <a href="https://www.sohu.com/a/891708273_122415321" target="_blank">张居正：明朝的改革者与历史的转折点_国家_措施_政策</a>
                        </div>
                    </div>
                </div>
                
                <div class="content negative">
                    <div class="summary">
                        <strong>反方观点：</strong>一条鞭法的财政收入增长来自压榨农民，改革依赖个人权威而非制度，死后政策即被废除，未能解决明朝根本问题。
                    </div>
                    <div class="citation">
                        <div class="citation-item">
                            <span class="citation-title">引用资料：</span>
                            <a href="https://www.sohu.com/a/383008362_120594864" target="_blank">从思想渊源施政原则角度，分析张居正为何人亡政息？三点不容忽视</a>
                        </div>
                        <div class="citation-item">
                            <a href="https://book.douban.com/review/14606784/" target="_blank">张居正的"人亡政息"和"人亡政存"辨（张居正大传）书评</a>
                        </div>
                        <div class="citation-item">
                            <a href="https://www.cssn.cn/lsx/lsx_zgs/202210/t20221024_5552514.shtml" target="_blank">从国家财政体制转型的视角看一条鞭法 - 中国社会科学网</a>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div class="buttons">
        <button class="btn" onclick="saveAsImage()">
            <i class="fas fa-camera"></i> 保存为图片
        </button>
        <button class="btn btn-save" onclick="saveAsHTML()">
            <i class="fas fa-file-code"></i> 保存为HTML
        </button>
    </div>

    <script>
        function saveAsImage() {
            const element = document.getElementById('capture');
            html2canvas(element, {
                backgroundColor: '#ffffff',
                scale: 2,
                logging: false,
                useCORS: true
            }).then(canvas => {
                const link = document.createElement('a');
                link.download = '张居正改革辩论摘要.png';
                link.href = canvas.toDataURL('image/png');
                link.click();
            });
        }

        function saveAsHTML() {
            const htmlContent = document.documentElement.outerHTML;
            const blob = new Blob([htmlContent], {type: 'text/html'});
            const url = URL.createObjectURL(blob);
            
            const link = document.createElement('a');
            link.href = url;
            link.download = '张居正改革辩论摘要.html';
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
        }
    </script>
</body>
</html>
```
    """
    load_dotenv()
    workspace = WorkSpace.from_oss_storages(workspace_id="d224b820-ad50-4440-af4b-924a98bc58ba")

    await workspace.add_artifact(
        CodeArtifact.build_artifact(
            artifact_id="result",
            content=content,
            code_type='html',
            metadata={
                "topic": "123123"
            }
        )
    )

    artifacts = workspace.list_artifacts()
    for artifact in artifacts:
        print(f"{artifact.artifact_type} : {artifact.artifact_id} : {artifact.content}")


    print(json.dumps(workspace.generate_tree_data(), indent=2, ensure_ascii=False))

if __name__ == '__main__':
    asyncio.run(main())