import {
  CloudUploadOutlined,
  CopyOutlined,
  DeleteOutlined,
  DislikeOutlined,
  LikeOutlined,
  PaperClipOutlined,
  PlusOutlined,
  QuestionCircleOutlined,
  ReloadOutlined
} from '@ant-design/icons';
import {
  Attachments,
  Bubble,
  Conversations,
  Sender,
  useXAgent,
  useXChat
} from '@ant-design/x';
import { Avatar, Button, Flex, type GetProp, message, Spin } from 'antd';
import { createStyles } from 'antd-style';
import dayjs from 'dayjs';
import React, { useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import logo from '../assets/aworld_logo.png';
import { useAgentId } from '../hooks/useAgentId';
import { useSessionId } from '../hooks/useSessionId';
import Prompts from '../pages/components/Prompts';
import Welcome from '../pages/components/Welcome';
import './index.less';

type BubbleDataType = {
  role: string;
  content: string;
};

// 添加会话数据类型定义
type SessionMessage = {
  role: string;
  content: string;
};

type SessionData = {
  user_id: string;
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  messages: SessionMessage[];
};

// 会话列表项类型
type ConversationItem = {
  key: string;
  label: string;
  group: string;
};

const DEFAULT_CONVERSATIONS_ITEMS: ConversationItem[] = [];

const SENDER_PROMPTS: GetProp<typeof Prompts, 'items'> = [];

const useStyle = createStyles(({ token, css }) => {
  return {
    layout: css`
      width: 100%;
      min-width: 1000px;
      height: 100vh;
      display: flex;
      background: ${token.colorBgContainer};
      font-family: AlibabaPuHuiTi, ${token.fontFamily}, sans-serif;
    `,
    // sider 样式
    sider: css`
      background: ${token.colorBgLayout}80;
      width: 280px;
      height: 100%;
      display: flex;
      flex-direction: column;
      padding: 0 12px;
      box-sizing: border-box;
    `,
    logo: css`
      display: flex;
      align-items: center;
      justify-content: start;
      box-sizing: border-box;
      gap: 8px;
      margin: 24px 0;

      span {
        font-weight: bold;
        color: ${token.colorText};
        font-size: 16px;
      }
    `,
    addBtn: css`
      background: #1677ff0f;
      border: 1px solid #1677ff34;
      height: 40px;
    `,
    conversations: css`
      flex: 1;
      overflow-y: auto;
      margin-top: 12px;
      padding: 0;

      .ant-conversations-list {
        padding-inline-start: 0;
      }
    `,
    siderFooter: css`
      border-top: 1px solid ${token.colorBorderSecondary};
      height: 40px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    `,
    // chat list 样式
    chat: css`
      height: 100%;
      width: 100%;
      box-sizing: border-box;
      display: flex;
      flex-direction: column;
      padding-block: ${token.paddingLG}px;
      gap: 16px;
    `,
    chatPrompt: css`
      .ant-prompts-label {
        color: #000000e0 !important;
      }
      .ant-prompts-desc {
        color: #000000a6 !important;
        width: 100%;
      }
      .ant-prompts-icon {
        color: #000000a6 !important;
      }
    `,
    chatList: css`
      flex: 1;
      overflow: auto;
    `,
    loadingMessage: css`
      background-image: linear-gradient(90deg, #ff6b23 0%, #af3cb8 31%, #53b6ff 89%);
      background-size: 100% 2px;
      background-repeat: no-repeat;
      background-position: bottom;
    `,
    placeholder: css`
      width: 100%;
      max-width: 700px;
      margin: 0 auto;
      height: 100%;
      display: flex;
      flex-direction: column;
      justify-content: center;
    `,
    // sender 样式
    sender: css`
      width: 100%;
      max-width: 700px;
      margin: 0 auto;
    `,
    speechButton: css`
      font-size: 18px;
      color: ${token.colorText} !important;
    `,
    senderPrompt: css`
      width: 100%;
      max-width: 700px;
      margin: 0 auto;
      color: ${token.colorText};
    `,
    sendButton: css`
      background-color: #000000 !important;
      border: none !important;
      transition: opacity 0.2s;
      
      &:hover {
        background-color: rgba(0, 0, 0, 0.7) !important;
      }
      
      &:disabled {
        opacity: 0.5 !important;
        cursor: not-allowed;
        background-color: rgba(0, 0, 0, 0.1) !important;
      }
      
      &:disabled:hover,
      &:disabled:focus {
        opacity: 0.5 !important;
        background-color: rgba(0, 0, 0, 0.1) !important;
      }
    `,
  };
});

const App: React.FC = () => {
  const { styles } = useStyle();
  const abortController = useRef<AbortController>(null);
  const { sessionId, generateNewSessionId, updateURLSessionId, setSessionId } = useSessionId();
  const { agentId, setAgentIdAndUpdateURL } = useAgentId();

  // ==================== State ====================
  const [messageHistory, setMessageHistory] = useState<Record<string, any>>({});
  const [sessionData, setSessionData] = useState<Record<string, SessionData>>({});

  const [conversations, setConversations] = useState<ConversationItem[]>(DEFAULT_CONVERSATIONS_ITEMS);
  const [curConversation, setCurConversation] = useState<string>('');

  const [attachmentsOpen, setAttachmentsOpen] = useState(false);
  const [attachedFiles, setAttachedFiles] = useState<GetProp<typeof Attachments, 'items'>>([]);

  const [inputValue, setInputValue] = useState('');
  // TODO mock data , remove in the future
  const [models, setModels] = useState<Array<{ label: string; value: string }>>([]);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [modelsLoading, setModelsLoading] = useState(false);
  // const [open, setOpen] = useState(false);




  // ==================== API Calls ====================
  const fetchModels = async () => {
    setModelsLoading(true);
    try {
      const response = await fetch('/api/agent/models');
      if (response.ok) {
        const data = await response.json();
        const modelOptions = Object.values(data).map((model: any) => ({
          label: model.name || model.id,
          value: model.id
        }));
        setModels(modelOptions);
      } else {
        message.error('Failed to fetch models');
      }
    } catch (error) {
      console.error('Error fetching models:', error);
      message.error('Error fetching models');
    } finally {
      setModelsLoading(false);
    }
  };

  const fetchSessions = async () => {
    try {
      const response = await fetch('/api/session/list');
      if (response.ok) {
        const sessions: SessionData[] = await response.json();

        // 将会话数据存储到 sessionData 状态中
        const sessionDataMap: Record<string, SessionData> = {};
        sessions.forEach(session => {
          sessionDataMap[session.id] = session;
        });
        setSessionData(sessionDataMap);

        // 转换为会话列表格式
        const conversationItems: ConversationItem[] = sessions.map(session => {
          // 生成会话标题：使用 name 字段，如果为空则使用第一条用户消息的前50个字符
          let label = session.name || session.description;
          if (!label && session.messages.length > 0) {
            const firstUserMessage = session.messages.find(msg => msg.role === 'user');
            if (firstUserMessage) {
              label = firstUserMessage.content.length > 50
                ? firstUserMessage.content.substring(0, 50) + '...'
                : firstUserMessage.content;
            } else {
              label = 'New Conversation';
            }
          }
          if (!label) {
            label = 'New Conversation';
          }

          return {
            key: session.id,
            label,
            group: '' // 移除分组
          };
        });

        // 按创建时间倒序排列（最新的在前面）
        conversationItems.sort((a, b) => {
          const sessionA = sessionDataMap[a.key];
          const sessionB = sessionDataMap[b.key];
          return dayjs(sessionB.created_at).valueOf() - dayjs(sessionA.created_at).valueOf();
        });

        setConversations(conversationItems);

        // 如果当前没有选中的会话，选择最新的一个
        if (!curConversation && conversationItems.length > 0) {
          const latestSession = conversationItems[0];
          setCurConversation(latestSession.key);

          // 更新sessionId以匹配选中的会话
          setSessionId(latestSession.key);
          updateURLSessionId(latestSession.key);

          // 加载该会话的消息历史
          const session = sessionDataMap[latestSession.key];
          if (session && session.messages.length > 0) {
            const chatMessages = session.messages.map((msg, index) => ({
              id: `${latestSession.key}-${index}`,
              message: {
                role: msg.role,
                content: msg.content
              },
              status: 'success' as const
            }));
            setMessages(chatMessages);
          }
        }
      } else {
        console.error('Failed to fetch sessions');
      }
    } catch (error) {
      console.error('Error fetching sessions:', error);
    }
  };

  // 初始化
  useEffect(() => {
    fetchModels();
    fetchSessions(); // 初始加载
  }, []);

  // 处理URL中的agentid参数与模型选择的同步
  useEffect(() => {
    if (agentId && models.length > 0) {
      // 检查URL中的agentid是否在models中存在
      const modelExists = models.find(model => model.value === agentId);
      if (modelExists) {
        setSelectedModel(agentId);
      } else {
        // 如果URL中的agentid不存在于models中，清除URL参数
        setAgentIdAndUpdateURL('');
      }
    }
  }, [agentId, models, setAgentIdAndUpdateURL]);

  // 处理模型选择变化时更新URL
  const handleModelChange = (modelId: string) => {
    setSelectedModel(modelId);
    setAgentIdAndUpdateURL(modelId);
  };

  /**
   * 🔔 Please replace the BASE_URL, PATH, MODEL, API_KEY with your own values.
   */

  // ==================== Runtime ====================
  const [agent] = useXAgent<BubbleDataType>({
    baseURL: '/api/agent/chat/completions',
    model: selectedModel, // 使用选中的模型
    dangerouslyApiKey: 'Bearer sk-xxxxxxxxxxxxxxxxxxxx',
  });
  const loading = agent.isRequesting();

  const { onRequest, messages, setMessages } = useXChat({
    agent,
    requestFallback: (_, { error }) => {
      if (error.name === 'AbortError') {
        return {
          content: 'Request is aborted',
          role: 'assistant',
        };
      }
      return {
        content: 'Request failed, please try again!',
        role: 'assistant',
      };
    },
    transformMessage: (info) => {
      const { originMessage, chunk } = info || {};
      let currentContent = '';
      let currentThink = '';
      try {
        if (chunk?.data && !chunk?.data.includes('DONE')) {
          const message = JSON.parse(chunk?.data);
          currentThink = message?.choices?.[0]?.delta?.reasoning_content || '';
          currentContent = message?.choices?.[0]?.delta?.content || '';
        }
      } catch (error) {
        console.error(error);
      }

      let content = '';

      if (!originMessage?.content && currentThink) {
        content = `<think>${currentThink}`;
      } else if (
        originMessage?.content?.includes('<think>') &&
        !originMessage?.content.includes('</think>') &&
        currentContent
      ) {
        content = `${originMessage?.content}</think>${currentContent}`;
      } else {
        content = `${originMessage?.content || ''}${currentThink}${currentContent}`;
      }
      return {
        content: content,
        role: 'assistant',
      };
    },
    resolveAbortController: (controller) => {
      abortController.current = controller;
    },
  });

  // ==================== Event ====================
  const onSubmit = (val: string) => {
    if (!val || !val.trim()) return;

    if (loading) {
      message.error('Request is in progress, please wait for the request to complete.');
      return;
    }

    onRequest({
      stream: true,
      message: { role: 'user', content: val },
      headers: {
        'X-Session-ID': sessionId,
      },
    });
  };

  // 复制消息内容到剪贴板
  const copyMessageContent = async (content: string) => {
    try {
      await navigator.clipboard.writeText(content);
      message.success('Message copied to clipboard');
    } catch (error) {
      console.error('Failed to copy message:', error);
      message.error('Failed to copy message');
    }
  };

  // 重新发送消息
  const resendMessage = (assistantMessageIndex: number) => {
    // 找到对应的用户消息
    const userMessageIndex = assistantMessageIndex - 1;
    if (userMessageIndex >= 0 && messages[userMessageIndex]?.message?.role === 'user') {
      const userMessage = messages[userMessageIndex].message.content;

      // 删除当前assistant消息和对应的用户消息
      const newMessages = messages.filter((_, index) => index !== assistantMessageIndex && index !== userMessageIndex);
      setMessages(newMessages);

      // 重新发送用户消息
      setTimeout(() => {
        onSubmit(userMessage);
      }, 100);
    } else {
      message.error('Cannot find corresponding user message');
    }
  };

  // const  = (status: boolean) => {
  //   setOpen(status);
  // }

  // ==================== Nodes ====================
  const chatSider = (
    <div className={styles.sider}>
      {/* 🌟 Logo */}
      <div className={styles.logo}>
        <img src={logo} alt="AWorld Logo" width="24" height="24" />
        <span>AWorld</span>
      </div>

      {/* 🌟 添加会话 */}
      <Button
        onClick={() => {
          if (agent.isRequesting()) {
            message.error(
              'Message is Requesting, you can create a new conversation after request done or abort it right now...',
            );
            return;
          }

          // 生成新的session ID
          const newSessionId = generateNewSessionId();

          // 创建新的会话项
          const newConversation: ConversationItem = {
            key: newSessionId,
            label: `New Conversation`,
            group: '', // 移除分组
          };

          setConversations([newConversation, ...conversations]);
          setCurConversation(newSessionId);
          setMessages([]);
        }}
        type="link"
        className={styles.addBtn}
        icon={<PlusOutlined />}
      >
        New Conversation
      </Button>

      {/* 🌟 会话管理 */}
      <Conversations
        items={conversations}
        className={styles.conversations}
        activeKey={curConversation}
        onActiveChange={async (val) => {
          abortController.current?.abort();
          // The abort execution will trigger an asynchronous requestFallback, which may lead to timing issues.
          // In future versions, the sessionId capability will be added to resolve this problem.
          setTimeout(() => {
            setCurConversation(val);

            // 更新sessionId以匹配选中的会话
            setSessionId(val);
            updateURLSessionId(val);

            // 优先从 sessionData 加载消息，如果没有则从 messageHistory 加载
            const session = sessionData[val];
            if (session && session.messages.length > 0) {
              const chatMessages = session.messages.map((msg, index) => ({
                id: `${val}-${index}`,
                message: {
                  role: msg.role,
                  content: msg.content
                },
                status: 'success' as const
              }));
              setMessages(chatMessages);
            } else {
              setMessages(messageHistory?.[val] || []);
            }
          }, 100);
        }}
        groupable={false}
        styles={{ item: { padding: '0 8px' } }}
        menu={(conversation) => ({
          items: [
            {
              label: 'Delete',
              key: 'delete',
              icon: <DeleteOutlined />,
              danger: true,
              onClick: () => {
                const newList = conversations.filter((item) => item.key !== conversation.key);
                const newKey = newList?.[0]?.key || '';
                setConversations(newList);

                // 从 sessionData 中删除对应的会话数据
                const newSessionData = { ...sessionData };
                delete newSessionData[conversation.key];
                setSessionData(newSessionData);

                // 从 messageHistory 中删除对应的消息历史
                const newMessageHistory = { ...messageHistory };
                delete newMessageHistory[conversation.key];
                setMessageHistory(newMessageHistory);

                // The delete operation modifies curConversation and triggers onActiveChange, so it needs to be executed with a delay to ensure it overrides correctly at the end.
                // This feature will be fixed in a future version.
                setTimeout(() => {
                  if (conversation.key === curConversation) {
                    setCurConversation(newKey);
                    if (newKey) {
                      // 更新sessionId
                      setSessionId(newKey);
                      updateURLSessionId(newKey);

                      // 优先从 sessionData 加载消息
                      const session = newSessionData[newKey];
                      if (session && session.messages.length > 0) {
                        const chatMessages = session.messages.map((msg, index) => ({
                          id: `${newKey}-${index}`,
                          message: {
                            role: msg.role,
                            content: msg.content
                          },
                          status: 'success' as const
                        }));
                        setMessages(chatMessages);
                      } else {
                        setMessages(newMessageHistory?.[newKey] || []);
                      }
                    } else {
                      setMessages([]);
                    }
                  }
                }, 200);
              },
            },
          ],
        })}
      />

      <div className={styles.siderFooter}>
        <Avatar size={24} />
        <Button type="text" icon={<QuestionCircleOutlined />} />
      </div>
    </div>
  );
  const chatList = (
    <div className={styles.chatList}>
      {messages?.length ? (
        /* 🌟 消息列表 */
        <Bubble.List
          items={messages?.map((i, index) => ({
            ...i.message,
            content: (
              <ReactMarkdown>
                {i.message.content || ''}
              </ReactMarkdown>
            ),
            classNames: {
              content: i.status === 'loading' ? styles.loadingMessage : '',
            },
            typing: i.status === 'loading' ? { step: 5, interval: 20, suffix: <>💗</> } : false,
            messageIndex: index,
          }))}
          style={{ height: '100%', paddingInline: 'calc(calc(100% - 700px) /2)' }}
          roles={{
            assistant: {
              placement: 'start',
              footer: (messageItem) => (
                <div style={{ display: 'flex' }}>
                  <Button
                    type="text"
                    size="small"
                    icon={<ReloadOutlined />}
                    onClick={() => resendMessage(messageItem.messageIndex)}
                  />
                  <Button
                    type="text"
                    size="small"
                    icon={<CopyOutlined />}
                    onClick={() => copyMessageContent(messageItem.content || '')}
                  />
                  {/* <Button
                    type="text"
                    size="small"
                    icon={<MenuUnfoldOutlined />}
                    onClick={() => onTriggerDraw(true)}
                  /> */}

                  <Button type="text" size="small" icon={<LikeOutlined />} />
                  <Button type="text" size="small" icon={<DislikeOutlined />} />
                </div>
              ),
              loadingRender: () => <Spin size="small" />,
            },
            user: { placement: 'end' },
          }}
        />
      ) : (
        <div
          className={styles.placeholder}
        >
          <Welcome
            onSubmit={(v: string) => {
              if (v && v.trim()) {
                onSubmit(v);
                setInputValue('');
              }
            }}
            models={models}
            selectedModel={selectedModel}
            onModelChange={handleModelChange}
            modelsLoading={modelsLoading}
          />
        </div>
      )}
    </div>
  );
  const senderHeader = (
    <Sender.Header
      title="Upload File"
      open={attachmentsOpen}
      onOpenChange={setAttachmentsOpen}
      styles={{ content: { padding: 0 } }}
    >
      <Attachments
        beforeUpload={() => false}
        items={attachedFiles}
        onChange={(info) => setAttachedFiles(info.fileList)}
        placeholder={(type) =>
          type === 'drop'
            ? { title: 'Drop file here' }
            : {
              icon: <CloudUploadOutlined />,
              title: 'Upload files',
              description: 'Click or drag files to this area to upload',
            }
        }
      />
    </Sender.Header>
  );
  const chatSender = (
    <>
      {/* 🌟 提示词 */}
      <Prompts
        items={SENDER_PROMPTS}
        onItemClick={(info) => {
          const description = info.data.description as string;
          if (description && description.trim()) {
            onSubmit(description);
          }
        }}
        className={styles.senderPrompt}
      />
      {/* 🌟 输入框 */}
      <Sender
        value={inputValue}
        header={senderHeader}
        onSubmit={() => {
          if (inputValue.trim()) {
            onSubmit(inputValue);
            setInputValue('');
          }
        }}
        onChange={setInputValue}
        onCancel={() => {
          abortController.current?.abort();
        }}
        prefix={
          <Button
            type="text"
            icon={<PaperClipOutlined style={{ fontSize: 18 }} />}
            onClick={() => setAttachmentsOpen(!attachmentsOpen)}
          />
        }
        loading={loading}
        className={styles.sender}
        allowSpeech
        actions={(_, info) => {
          const { SendButton, LoadingButton, SpeechButton } = info.components;
          return (
            <Flex gap={4}>
              <SpeechButton className={styles.speechButton} />
              {loading ? (
                <LoadingButton type="default" />
              ) : (
                <SendButton
                  type="primary"
                  disabled={!inputValue.trim()}
                  className={styles.sendButton}
                />
              )}
            </Flex>
          );
        }}
        placeholder="Ask or input / use skills"
      />
    </>
  );

  useEffect(() => {
    // history mock
    if (messages?.length && curConversation) {
      setMessageHistory((prev) => ({
        ...prev,
        [curConversation]: messages,
      }));
    }
  }, [messages, curConversation]);

  // ==================== Render =================
  return (
    <div className={styles.layout}>
      {chatSider}
      <div className={styles.chat}>
        {chatList}
        {(messages?.length > 0 || curConversation) && chatSender}
      </div>
    </div>
  );
};

export default App;