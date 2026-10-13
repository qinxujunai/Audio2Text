# SenseVoice CUDA 人工参考测评

2026-10-12，RTX 3060 Laptop 6GB，SenseVoice Small FP32，固定模型和 CUDA SDK。使用 [ASCEND 官方项目](https://github.com/HLTCHKUST/ASCEND) 与其 [公开数据集](https://huggingface.co/datasets/CAiRE/ASCEND)，固定 revision `737e9800ae31be9932ba8464c80366559bd28424`。数据许可 CC BY-SA 4.0；参考为语料人工标注，不是另一个识别模型的输出。

5735 段按音频哈希去重，总 4.587 小时，每段重复三轮，共 17205 次；无运行失败。

| 场景 | 独立片段 | 指标 | 语料总体错误率 |
| --- | ---: | --- | ---: |
| 普通话 | 2953 | CER，字符错误率 | 9.31% |
| 英文 | 1261 | WER，词错误率 | 23.34% |
| 中英文混说 | 1521 | MER，汉字与英文词混合单位错误率 | 15.52% |

按总编辑距离除以总参考单位计算，进行 NFKC/小写规范化；CER 保留字母、数字与组合标记，排除空白、标点和符号；WER/MER 保留英文词及单个汉字单位。三轮相同输出不会使独立样本量增大。这些是错误率，不转换为泛化准确率或“正确率=100%-错误率”。原 CLI 另有包含标点的逐片均值，与本表口径不同，不能混用。

此语料是香港双语对话，不代表用户所有长视频、方言、背景音乐或噪声条件。缺完整关键切片、平台提取和干净 Windows 证据，发布门槛仍未通过。其他构建/测试曾与本轮测评并行，因此耗时只作运行记录，不用来宣称受控加速比；GPU 进程显存无法从 WDDM 获得，记为未知。

本地原始报告 `tests_runtime/product-upgrade/ascend-full-sensevoice.json`、逐条检查点 `ascend-full-sensevoice-checkpoint.jsonl`、规范化总体结果 `ascend-aggregate.json`。公平同音频 Whisper 对比及相同 FP32 权重 CPU/CUDA 比较分别运行，不能从上述单模型测评得出谁最好或 CUDA 快几倍。

## 同音频双模型复测

另取 72 段（每种语言 24 段，共 5.34 分钟），两模型均走 CUDA，三轮。Whisper medium FP16、beam=1、VAD 开启；SenseVoice FP32、原生解码与分块。已修复旧测评的英文 segment 空格拼接和模型释放问题，此次结果替代旧的 72 段试验。不同引擎的原生策略不完全相同，反映这两个实际配置的结果，不称为只改变模型权重的消融试验。

| 指标 | Whisper medium CUDA | SenseVoice FP32 CUDA |
| --- | ---: | ---: |
| 普通话总体 CER | 37.50% | 12.75% |
| 英文总体 WER | 30.18% | 25.09% |
| 混说总体 MER | 36.05% | 16.32% |
| 三轮总推理耗时 | 137.785s | 23.452s |
| 运行失败 | 0 | 0 |

此小样本支持保留当前 SenseVoice CUDA 默认路线，没有支持替换为本机 medium Whisper 的证据；不能推断全部视频都如此、Whisper 所有型号都如此，或正式准确率门槛已通过。两模型耗时之比属于产品配置比较，不是纯 CUDA 加速比。原始结果 `ascend-balanced-fair.json`，总体规范化结果 `ascend-balanced-fair-aggregate.json`。

## 相同权重的 CPU/CUDA 测试

同一份约 180 秒音频、同一 FP32 `model.onnx`、同一 SDK、同一分块、batch=1，预加载后分别三轮。CPU 中位推理耗时 12.872 秒，CUDA 中位 1.018 秒，约 12.65 倍；CPU/CUDA batch=1 输出逐块相同。batch=4 的 CUDA 中位 1.168 秒，此样本未显示增加 batch 更快。

计时范围为已解码音频块的推理，排除模型加载、媒体解码、下载与写文件；不能乘以 12.65 推断整个四小时任务的端到端耗时。该用户片段没有人工参考，不从文本一致推导准确率。原始记录 `tests_runtime/product-upgrade/same-fp32-cpu-cuda-20261012.json`。

## 九类场景的证据覆盖

另完成官方 AMI 人工注释会议三轮（多说话人、远场和连续长段）、Google FLEURS 粤语验证集 362 段三轮，以及 24 段人工参考语音叠加 MUSAN 真实无歌词音乐的 0/10/20dB SNR 三轮。音乐记录按 MUSAN 的逐录音许可证与无歌词标注选择；来源为 Airglow《Memory Bank》，保留 Attribution License。这是明确标注的受控鲁棒性实验，不声称原语料天然带音乐。

专有名词切片使用人工参考中的固定名称列表，先按参考选取 48 段，不依据模型输出挑选；保留这些音频在原测评中的结果，只改变评测分组。组合报告保留全部来源，音乐变体、同一会议的第二麦克风和重叠长段不增加独立样本数或时长。核对后为 6098 段独立音频、6.082 小时、每段三轮，九类分组齐全，无运行失败。

AMI 的同时说话标注按词起始时间排序，单序列参考不能完全表达重叠；FLEURS 粤语参考为繁体书面提示，原始计分包含输出字形差异；音乐实验不能替代真实现场的全部音乐条件。各切片的原 CLI 均值与前文语料总体指标口径不同。逐条覆盖检查通过，只证明数据与三轮记录完整，不证明产品在所有场景准确，更不代表新模型替换资格通过。

组合报告为 `tests_runtime/product-upgrade/sensevoice-all-slices-evidence.json`。六个平台的 60 条实链、干净 Windows 和最终独立发布复核仍待完成。
