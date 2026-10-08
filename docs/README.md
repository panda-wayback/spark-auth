# 功能索引

## 服务端

- [卡密管理](server/key-management/README.md)：按软件签发自包含卡密，并控制卡密何时可用、何时失效。
- [设备绑定](server/device-binding/README.md)：一个卡密同一时刻只在一台设备上生效，换设备按 product 策略处理并留下记录。
- [按次数核销](server/count-redeem/README.md)：让接入方用按次数的卡密为任务收费；可查剩余、一次扣多次，用完或超用后卡密作废。
- [管理后台](server/admin-console/README.md)：个人使用的极简网页后台，完成全部管理操作。
- [部署运行](server/deployment/README.md)：本机一条命令、镜像只挂一个数据卷即可运行，便于作为其它项目的依赖部署多个实例。

## 客户端接入规范（对外）

- [激活接入规范](client/activation/README.md)：规定接入方软件如何调用服务端完成激活与校验。
- [设备指纹规范](client/fingerprint/README.md)：规定接入方软件如何跨平台采集设备标识并生成统一格式的设备指纹。
- [核销接入规范](client/redeem/README.md)：规定接入方如何查询与核销按次数卡密。
