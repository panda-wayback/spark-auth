# 设备指纹规范

## 目标

规定接入方软件如何跨平台采集不容易变、容易获取的设备标识，并生成统一格式的设备指纹。本仓库不提供实现。

## 要实现的

- Windows：读取主板 UUID（`Win32_ComputerSystemProduct.UUID`）。
- macOS：读取 `IOPlatformUUID`。
- Linux：优先读取 `/sys/class/dmi/id/product_uuid`，失败时降级读取 `/etc/machine-id`。
- 原始标识在本地做 SHA-256 哈希，上传 64 位小写十六进制字符串作为 device_hash。

## 关键约束

- 服务端只接收并存储 device_hash，不收集原始硬件信息；格式不符的 device_hash 被服务端拒绝。
- 同一设备多次生成的 device_hash 必须一致。
- 采集失败时报错，不降级到主机名、用户名等易改字段。

## 失败场景

- 当前平台无法读取主板 UUID 或 machine-id。
- 用户篡改上传的 device_hash（仅影响本机，无法增加授权名额）。

## 解决步骤

1. 按平台读取上述设备标识。
2. 对原始标识做 SHA-256 哈希，得到 device_hash。
3. 激活与校验时上报 device_hash。
