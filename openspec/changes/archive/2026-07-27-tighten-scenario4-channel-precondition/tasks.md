# Tasks — tighten-scenario4-channel-precondition

## 1. 规约修订

- [ ] 1.1 据 R2 评审 P1（场景 4 仍把通道保活测试写成 admitted 路径直接锁定），再次软化场景 4 THEN 子句：测试直接锁定的是「有限 prompt 输入在默认配置下不在 result 前关闭共享控制通道」——这是 admitted/denied 共享的通道可用前置条件，不直接 exercise 任一 permission outcome（FakeTransport 不发 control request，_admit 从未被调用，无 response 写入）；path-specific 结果验证 + 真实 Node-command canary 延期 dispatch 自然验证

## 2. 验证

- [ ] 2.1 `openspec validate tighten-scenario4-channel-precondition` 通过
- [ ] 2.2 纯文档变更：无代码/测试改动，既有 1243 passed / 6 xfailed 不受影响

## 3. 规约同步

- [ ] 3.1 `/opsx:sync` delta → main spec
- [ ] 3.2 `/opsx:archive`
