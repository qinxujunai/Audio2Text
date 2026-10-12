# ASR 模型量化审计（2026-10-11）

本报告纠正此前未经充分量化的模型推荐。当前不能声称 SenseVoice Small 或 Whisper medium 是本产品所有中英文场景的最佳默认模型，也不能把不同模型、不同设备的耗时差异称为 CUDA 加速收益。未替换正在运行的安装版。

## 指标

- 中文 CER：替换、删除和插入字符的总数 / 标准文本字符数。
- 英文 WER：替换、删除和插入单词的总数 / 标准文本单词数。
- 中英混说 MER：中文按字、英文按词混合计分。
- 三项均越低越好，不能混成一个不注明数据集的“准确率”。不同计分单位不可跨行直接比较。
- 识别文本和参考文本必须使用一致、预先固定的规范化；标点、大小写、繁简体和数字写法的规则应公开。语义相同的“九点/9点”应与数字值识别错误分开报告。

## 公开评测（不是本机测得的性能）

| 场景与数据集 | 指标 | SenseVoice Small | Whisper 具体版本 | Whisper 错误率 |
| --- | --- | ---: | --- | ---: |
| 中文朗读 AISHELL-1 test | CER | 2.96% | large-v3 | 5.14% |
| 中文会议 WenetSpeech test_meeting | CER | 7.44% | large-v3 | 18.87% |
| 英文朗读 LibriSpeech test_clean | WER | 3.15% | large-v3 | 1.82% |
| 英文较难录音 LibriSpeech test_other | WER | 7.18% | large-v3 | 3.50% |
| 中英自发对话 CS-Dialogue | MER | 6.71% | medium，未针对该数据集微调 | 15.88% |
| 中英混说 SwitchLingua Mandarin-English | CER | 40.08% | large-v3 | 17.03% |

来源和可复核位置：

1. [FunAudioLLM，表 6](https://arxiv.org/html/2407.04051v2#S4.T6)：前四行；模型作者的评测，非独立第三方复测。表 7 的速度使用 A800、batch=1、Whisper beam=5，不能套用到 RTX 3060 或 faster-whisper 批处理。
2. [CS-Dialogue，表 8、9](https://arxiv.org/html/2502.18913v2)：第五行。测试集条件和模型实现以论文为准。
3. [SwitchLingua NeurIPS 2025，表 3，第 6 页](https://proceedings.neurips.cc/paper_files/paper/2025/file/4847447cae911dde321ca19afe7d8707-Paper-Datasets_and_Benchmarks_Track.pdf)：第六行。依次核对列名 Qwen 原始、Qwen 清理、SenseVoice、Seamless、Wav2Vec2、Whisper，避免搜索摘要错排列名。

解释：这些中文数据集有利于 SenseVoice，英文数据集有利于 Whisper large-v3；混说数据集的胜负不同。CS-Dialogue 对手是 medium，SwitchLingua 对手是 large-v3，计分方式也不同，不能合并成一个排行榜，也不能把 large-v3 的质量数字套到本机 medium。

## 本机已有参考片段的复测

报告：`workspace/benchmarks/model-audit-20261011-single-reference.json`。两个模型各重复三次，但只有一个唯一中文音频，5.592 秒。标准文本来自已有 manifest，并不是本次重新人工听写的标注。

原参考：`开放时间早上9点至下午5点`。

- Whisper medium 输出：`开放时间早上9点至下午5点`，原计分 CER 0%。
- SenseVoice int8 输出：`放时间早上九点至下午五点`，原计分 CER 23.08%。
- 对这一个片段明确接受数字等价写法后：SenseVoice 真正剩余的是遗漏“开”，CER 为 1/13 = 7.69%；Whisper 仍为 0%。这不是通用数字规范化器或发布质量结果。

设备不同的耗时只可记录各自运行状态，不作为模型速度公平比较或 CUDA 加速收益。该片段不足以预测中文会议、英文或混说准确率。此前的三分钟音频没有人工参考，也只证明运行稳定与耗时，不提供准确率。

## 当前决策与尚缺证据

经用户确认，落实 SenseVoice Small CUDA，Whisper 留为备用。原 CPU 兼容模型保留。GPU 运行路线、权重版本和可选 AI 校对是三个独立决策。

已完成 SenseVoice CUDA 运行与同权重速度验证，详见 `local-cuda-proofreading-plan.md` 及 `workspace/benchmarks/sensevoice-same-model-cuda-20261011.json`。FP32 CUDA 在同一个 5.592 秒参考片段输出 `开放时间早上9点至下午5点。`，去除标点后 CER 0%；这只有一个样本，不能外推整体质量。原安装 int8 为 WSYue 粤语微调版，新 FP32 为通用 SenseVoice Small，不能混称为相同权重。

本机仍缺：中文、英文、中英混说分别覆盖多个说话人和录音条件的人工参考；漏字、数字和专有名词分项错误；规范化后按参考字数/词数汇总的 corpus 错误率和不确定性。所有未测项目保持“未测”，不填估计百分比。单个四小时转写验证不等于仓库既有的 72 片段、4 小时、多场景及三轮模型发布质量门槛。
