---
name: domestic-integration
description: 国内生态集成 — 微信、支付宝、阿里云、腾讯云等国内服务
allowed-tools: [Read, Write, Edit, Bash]
model: glm-5
triggers: [微信, 支付宝, 阿里云, 腾讯云, 华为云, 国内, wechat, alipay, wecom, 企业微信, 钉钉, oss, cos]
tags: [domestic, china, integration, payment, cloud]
priority: 10
---

# 国内生态集成技能

## 支持的服务

### 支付
- 微信支付（WeChat Pay）：JSAPI、Native、H5、小程序支付
- 支付宝（Alipay）：网页支付、手机网站支付、APP 支付

### 云服务
- 阿里云（Alibaba Cloud）：OSS、RDS、ECS、函数计算
- 腾讯云（Tencent Cloud）：COS、云数据库、云函数
- 华为云（Huawei Cloud）：OBS、GaussDB

### 消息推送
- 微信公众号消息
- 企业微信（WeCom）机器人
- 钉钉（DingTalk）机器人

### 短信
- 阿里云短信服务
- 腾讯云短信

## 集成规范

1. API 密钥通过环境变量注入，不硬编码
2. 使用官方 SDK 而非直接调用 HTTP API
3. 实现重试机制和错误处理
4. 记录关键操作日志（脱敏处理）

## 示例：企业微信机器人

```python
import httpx

async def send_wecom_message(webhook_url: str, content: str) -> bool:
    """发送企业微信机器人消息。"""
    payload = {
        "msgtype": "text",
        "text": {"content": content}
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(webhook_url, json=payload)
        return resp.json().get("errcode") == 0
```
