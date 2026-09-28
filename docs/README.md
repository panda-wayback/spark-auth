# 软件激活能力

## 目标

实现基于密钥（key）与设备绑定的软件激活能力：用户输入 key 后，服务端校验 key 有效性并把当前设备设为授权设备，防止 key 被复制到多台机器上滥用。

## 要实现的

- 管理员按软件创建 product，并为每个 product 配置换设备策略（是否允许换设备、换设备扣减时长）。
- 管理员在 product 下单个或批量生成 key 并设置有效时长（从首次激活开始计算）；支持导入外部 key、导出 key。
- 服务端使用 Python + Django，数据存储使用 SQLite。
- 管理员通过网页登录管理后台完成全部管理操作。
- 用户在接入方软件中输入 key 激活；客户端不在本仓库实现，接入方按客户端规范调用服务端接口。
- 一个 key 同一时刻只绑定一台设备；按 product 策略允许或拒绝换设备，允许时扣减剩余时长。
- 每个 key 的每次换设备都有记录，管理员可在后台查看。
- 服务端为客户端提供激活、校验接口。
- 接入方跨平台采集设备指纹并本地哈希后上传。

## 解决步骤

1. [服务端 key 管理](server/key-management/README.md)
2. [服务端设备绑定](server/device-binding/README.md)
3. [服务端管理后台](server/admin-console/README.md)
4. [客户端设备指纹规范](client/fingerprint/README.md)
5. [客户端激活接入规范](client/activation/README.md)
